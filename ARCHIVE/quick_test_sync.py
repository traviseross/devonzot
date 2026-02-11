#!/usr/bin/env python3
"""
Find actual Zotero items to test with
"""

import sys
sys.path.append('/Users/travisross/DEVONzot')
from production_metadata_sync import ZoteroDevonthinkMetadataSync
import sqlite3
from pathlib import Path

def find_zotero_items_for_testing():
    """Find actual Zotero items we can test with"""
    syncer = ZoteroDevonthinkMetadataSync()
    
    try:
        conn = syncer.get_zotero_connection()
        
        # Find items that match our test file name pattern
        query = """
        SELECT i.key, iv.value as title, 
               GROUP_CONCAT(CASE WHEN f.fieldName = 'publicationTitle' THEN iv2.value END) as publication,
               GROUP_CONCAT(CASE WHEN f.fieldName = 'date' THEN iv2.value END) as date
        FROM items i
        LEFT JOIN itemData id ON i.itemID = id.itemID
        LEFT JOIN itemDataValues iv ON id.valueID = iv.valueID AND id.fieldID = 110  -- title
        LEFT JOIN itemData id2 ON i.itemID = id2.itemID
        LEFT JOIN fields f ON id2.fieldID = f.fieldID
        LEFT JOIN itemDataValues iv2 ON id2.valueID = iv2.valueID
        WHERE iv.value LIKE '%Henderson%' OR iv.value LIKE '%Black%Market%'
        GROUP BY i.key, iv.value
        LIMIT 5
        """
        
        results = conn.execute(query).fetchall()
        
        print("🔍 Found Zotero items for testing:")
        print("=" * 50)
        
        for result in results:
            print(f"Key: {result['key']}")
            print(f"Title: {result['title']}")
            print(f"Publication: {result['publication']}")
            print(f"Date: {result['date']}")
            print("-" * 30)
        
        conn.close()
        
        if results:
            return results[0]['key']  # Return first key for testing
        else:
            return None
            
    except Exception as e:
        print(f"Error: {e}")
        return None

def quick_test():
    """Quick test with found Zotero key"""
    print("🚀 Quick Metadata Test")
    print("=" * 30)
    
    zotero_key = find_zotero_items_for_testing()
    
    if not zotero_key:
        print("❌ No suitable Zotero items found for testing")
        return
    
    print(f"\n🔑 Testing with Zotero key: {zotero_key}")
    
    # Test the metadata retrieval
    syncer = ZoteroDevonthinkMetadataSync()
    metadata = syncer.get_zotero_metadata(zotero_key)
    
    if metadata:
        print(f"\n📄 Retrieved metadata:")
        print(f"   Title: {metadata['title']}")
        print(f"   Author: {metadata['author']}")
        print(f"   Publication: {metadata['publication']}")
        print(f"   Year: {metadata['year']}")
        print(f"   Type: {metadata['type']}")
        print(f"   Description: {metadata['description'][:100]}...")
        
        # Generate tags to show what would be applied
        tags = []
        
        if metadata['type']:
            tags.append(metadata['type'])
        if metadata['publication']:
            tags.append(metadata['publication'])
        if metadata['year'] and metadata['year'].isdigit():
            year = int(metadata['year'])
            decade = (year // 10) * 10
            tags.append(f"{decade}s")
        
        # Check for economics keywords
        content_text = f"{metadata['title']} {metadata['description']}".lower()
        econ_terms = ['economics', 'economic', 'market', 'trade', 'regulation', 'policy']
        if any(term in content_text for term in econ_terms):
            tags.append('economics')
        
        print(f"\n🏷️  Tags that would be applied: {', '.join(tags)}")
        
        # Test manually applying to our DEVONthink record
        test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
        
        print(f"\n❓ Apply this metadata to DEVONthink record {test_uuid}?")
        response = input("   (y/n): ").lower().strip()
        
        if response == 'y':
            # Apply macOS metadata
            file_path = "/Users/travisross/DEVONthink/Articles.dtBase2/Files.noindex/pdf/19/Henderson - How Black Is Our Market - The Atlantic - 1946 - Magazine Article.pdf"
            if Path(file_path).exists():
                print("📝 Setting macOS metadata...")
                macos_success = syncer.set_macos_metadata(file_path, metadata)
                print(f"   macOS metadata: {'✅' if macos_success else '❌'}")
            
            # Apply DEVONthink tags
            print("🏷️  Setting DEVONthink tags...")
            dt_success = syncer.set_devonthink_tags(test_uuid, tags)
            print(f"   DEVONthink tags: {'✅' if dt_success else '❌'}")
            
            if dt_success or macos_success:
                print("\n🎉 Test sync completed!")
                print(f"   Open x-devonthink-item://{test_uuid} to see results")
            
    else:
        print("❌ No metadata found for this key")

if __name__ == "__main__":
    quick_test()