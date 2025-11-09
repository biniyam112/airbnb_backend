import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, PROJECT_ROOT)

from config.db import get_db  # type: ignore
from bson.objectid import ObjectId

db = get_db()
prop = db.property.find_one({'_id': ObjectId('6739f7d59e7b23a74b1f89c3')})

if prop:
    images = prop.get('images', [])
    print(f'Found property: {prop.get("title")}')
    print(f'Image count: {len(images)}')
    print(f'Images: {images}')
else:
    print('Property not found')

# Check all properties with != 5 images
print('\n=== Properties with != 5 images ===')
cursor = db.property.find()
for p in cursor:
    img_count = len(p.get('images', []))
    if img_count != 5:
        print(f'{p["_id"]}: {img_count} images - {p.get("title", "No title")}')
