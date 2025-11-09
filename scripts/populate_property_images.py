"""Populate existing properties with 5 unique random images each.

Usage (from project root):
    python backend/scripts/populate_property_images.py

Options:
    --overwrite    If set, replaces any existing images list instead of merging missing ones.
    --dry-run      Show planned changes without writing to the database.

The script imports the Unsplash image URL list from image_population.py.
"""
from __future__ import annotations
import os
import sys
import random
import argparse
from datetime import datetime

# Ensure we can import config and image_population
SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, PROJECT_ROOT)

from config.db import get_db  # type: ignore
from scripts.image_population import image_urls  # type: ignore

REQUIRED_IMAGES_PER_PROPERTY = 5


def choose_unique_images(existing: list[str] | None, pool: list[str], required: int, keep_existing: bool) -> list[str]:
    """Return a list of images ensuring uniqueness within the property.

    By default, always replaces with fresh random images from the pool.
    If keep_existing is True and existing images already satisfy requirement, returns existing.
    Otherwise samples without replacement from pool.
    """
    existing = existing or []
    
    # If keeping existing and they satisfy the requirement, return them
    if keep_existing and len(existing) >= required:
        normalized = list(dict.fromkeys(existing))[:required]
        if len(normalized) == required:
            return normalized

    # Always get fresh images from pool (default behavior)
    if len(pool) < required:
        raise RuntimeError(f"Not enough distinct images in pool (needed={required}, available={len(pool)})")

    return random.sample(pool, required)


def process_properties(db, keep_existing: bool, dry_run: bool, only_missing: bool, limit: int | None, verbose: bool) -> dict:
    updated_count = 0
    skipped_count = 0  # errors or not enough images
    unchanged_count = 0  # already satisfied requirement
    details = []

    query = {}
    if only_missing:
        query = {"$or": [
            {"images": {"$exists": False}},
            {"images": {"$size": 0}},
            {"$expr": {"$lt": [{"$size": {"$ifNull": ["$images", []]}}, REQUIRED_IMAGES_PER_PROPERTY]}}
        ]}

    cursor = db.property.find(query)
    if limit:
        cursor = cursor.limit(int(limit))

    for prop in cursor:
        existing = prop.get('images')
        try:
            new_images = choose_unique_images(existing, image_urls, REQUIRED_IMAGES_PER_PROPERTY, keep_existing)
        except RuntimeError as e:
            skipped_count += 1
            details.append({'id': str(prop.get('_id')), 'status': 'skipped', 'reason': str(e)})
            continue

        # Normalize existing to compare: de-dup and truncate to required
        normalized_existing = list(dict.fromkeys(existing or []))[:REQUIRED_IMAGES_PER_PROPERTY]
        
        # Check if update is needed (with keep_existing, skip if already satisfied)
        if keep_existing and normalized_existing == new_images and len(existing or []) == REQUIRED_IMAGES_PER_PROPERTY:
            unchanged_count += 1
            details.append({'id': str(prop.get('_id')), 'status': 'unchanged', 'count': len(normalized_existing)})
            if verbose:
                print(f"[unchanged] {prop.get('_id')} already has exactly {len(normalized_existing)} unique images")
            continue

        if dry_run:
            details.append({'id': str(prop.get('_id')), 'status': 'planned', 'before': normalized_existing, 'after': new_images})
            if verbose:
                print(f"[plan] {prop.get('_id')} -> {len(new_images)} images")
            continue

        db.property.update_one({'_id': prop['_id']}, {'$set': {'images': new_images, 'updatedAt': datetime.utcnow()}})
        updated_count += 1
        details.append({'id': str(prop.get('_id')), 'status': 'updated', 'count': len(new_images)})
        if verbose:
            print(f"[update] {prop.get('_id')} -> {len(new_images)} images")

    return {
        'updated': updated_count,
        'skipped': skipped_count,
        'unchanged': unchanged_count,
        'dry_run': dry_run,
        'details': details,
    }


def main():
    parser = argparse.ArgumentParser(description='Populate property images with fresh random URLs (default behavior).')
    parser.add_argument('--keep-existing', action='store_true', help='Keep existing images if they already satisfy the requirement (old behavior).')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without writing.')
    parser.add_argument('--only-missing', action='store_true', help='Process only properties with fewer than required images.')
    parser.add_argument('--limit', type=int, default=None, help='Limit number of properties to process.')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompts.')
    parser.add_argument('--verbose', action='store_true', help='Print per-property actions.')
    args = parser.parse_args()

    db = get_db()
    
    # Default behavior: always replace images (unless --keep-existing or --only-missing)
    # Optional confirmation when replacing many records
    if not args.keep_existing and not args.only_missing and not args.dry_run and not args.yes:
        total = db.property.count_documents({})
        target = total if not args.limit else min(total, args.limit)
        msg = f"This will replace images for {target} properties with fresh random URLs. Continue? [y/N]: "
        try:
            confirm = input(msg)
        except EOFError:
            confirm = 'n'
        if confirm.strip().lower() not in ('y', 'yes'):
            print('Aborted by user.')
            return

    result = process_properties(
        db,
        keep_existing=args.keep_existing,
        dry_run=args.dry_run,
        only_missing=args.only_missing,
        limit=args.limit,
        verbose=args.verbose,
    )

    print('\n=== Property Image Population Summary ===')
    print(f"Updated   : {result['updated']}")
    print(f"Unchanged : {result['unchanged']}")
    print(f"Skipped   : {result['skipped']}")
    print(f"Dry Run   : {result['dry_run']}")
    sample = result['details'][:5]
    if sample:
        print('\nSample details:')
        for d in sample:
            print(f" - {d}")
    print('\nDefault: Always replaces images with fresh random URLs.')
    print('Use --dry-run first to preview changes.')
    print('Use --keep-existing to skip properties that already have valid images.\n')


if __name__ == '__main__':
    main()
