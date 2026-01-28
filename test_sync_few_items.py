#!/usr/bin/env python3
"""
Test the production metadata sync on a few items first
"""

import sys
sys.path.append('/Users/travisross/DEVONzot')
from production_metadata_sync import ZoteroDevonthinkMetadataSync
import logging

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_on_few_items():
    """Test metadata sync on just the first few items"""
    syncer = ZoteroDevonthinkMetadataSync()
    
    print("🧪 Testing Metadata Sync on First Few Items")
    print("=" * 60)
    
    # Get all records with Zotero links
    print("📋 Finding DEVONthink records with Zotero links...")
    records = syncer.get_devonthink_records_with_zotero_links()
    
    if not records:
        print("❌ No records found with Zotero links!")
        return
    
    print(f"✅ Found {len(records)} total records with Zotero links")
    
    # Test on first 3 items
    test_records = records[:3]
    print(f"\n🎯 Testing on first {len(test_records)} records:")
    
    for i, record in enumerate(test_records, 1):
        print(f"\n--- Record {i} ---")
        print(f"Name: {record['name']}")
        print(f"DEVONthink UUID: {record['devonthink_uuid']}")
        print(f"Zotero Key: {record['zotero_key']}")
        print(f"Path: {record['path']}")
        
        # Get what metadata would be set
        if record['zotero_key']:
            metadata = syncer.get_zotero_metadata(record['zotero_key'])
            if metadata:
                print(f"\n📄 Zotero Metadata:")
                print(f"   Title: {metadata['title']}")
                print(f"   Author: {metadata['author']}")
                print(f"   Publication: {metadata['publication']}")
                print(f"   Year: {metadata['year']}")
                print(f"   Type: {metadata['type']}")
                print(f"   Description: {metadata['description'][:100]}..." if len(metadata['description']) > 100 else f"   Description: {metadata['description']}")
                
                # Generate tags to show what would be applied
                tags = []
                
                # Add exact Zotero item type as tag
                if metadata['type']:
                    tags.append(metadata['type'])
                
                # Add publication name
                if metadata['publication']:
                    tags.append(metadata['publication'])
                
                # Add decade tag
                if metadata['year'] and metadata['year'].isdigit():
                    year = int(metadata['year'])
                    decade = (year // 10) * 10
                    tags.append(f"{decade}s")
                
                # Extract thematic tags (simplified for display)
                content_text = f"{metadata['title']} {metadata['description']}".lower()
                
                # Check for economics
                econ_terms = ['economics', 'economic', 'market', 'trade', 'regulation', 'policy']
                if any(term in content_text for term in econ_terms):
                    tags.append('economics')
                
                print(f"\n🏷️  Tags that would be applied: {', '.join(tags)}")
        
        print(f"\n🔗 DEVONthink Link: x-devonthink-item://{record['devonthink_uuid']}")
    
    # Ask if user wants to proceed with actual sync
    print(f"\n❓ Ready to apply metadata sync to these {len(test_records)} items?")
    print("   This will:")
    print("   • Set macOS native metadata (author, title, description)")  
    print("   • Apply smart tags in DEVONthink")
    print("   • Update DEVONthink records")
    
    response = input("\nProceed? (y/n): ").lower().strip()
    
    if response == 'y':
        print(f"\n🚀 Applying metadata sync...")
        success_count = 0
        
        for record in test_records:
            try:
                if syncer.sync_metadata_for_record(record):
                    success_count += 1
                    print(f"✅ Synced: {record['name']}")
                else:
                    print(f"❌ Failed: {record['name']}")
            except Exception as e:
                print(f"❌ Error syncing {record['name']}: {e}")
        
        print(f"\n🎉 Test sync complete!")
        print(f"   Successfully synced: {success_count}/{len(test_records)}")
        print(f"\n📝 Next steps:")
        print(f"   1. Check DEVONthink Info panels for the synced items")
        print(f"   2. Try searching by the new tags")
        print(f"   3. If happy with results, run full sync with production_metadata_sync.py")
        
    else:
        print("🚫 Test cancelled. No changes made.")

if __name__ == "__main__":
    test_on_few_items()