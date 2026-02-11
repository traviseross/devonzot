#!/usr/bin/env python3
"""
Find any Zotero items for testing
"""

import sys
sys.path.append('/Users/travisross/DEVONzot')
from production_metadata_sync import ZoteroDevonthinkMetadataSync

def find_any_zotero_items():
    """Find any Zotero items we can use for testing"""
    syncer = ZoteroDevonthinkMetadataSync()
    
    try:
        conn = syncer.get_zotero_connection()
        
        # Get some recent items with titles
        query = """
        SELECT i.key, iv.value as title
        FROM items i
        JOIN itemData id ON i.itemID = id.itemID
        JOIN itemDataValues iv ON id.valueID = iv.valueID
        WHERE id.fieldID = 110  -- title field
        AND iv.value IS NOT NULL
        AND iv.value != ''
        ORDER BY i.itemID DESC
        LIMIT 10
        """
        
        results = conn.execute(query).fetchall()
        
        print("📚 Available Zotero items:")
        print("=" * 50)
        
        for i, result in enumerate(results, 1):
            key = result['key']
            title = result['title'][:60] + "..." if len(result['title']) > 60 else result['title']
            print(f"{i:2d}. {key} - {title}")
        
        conn.close()
        
        if results:
            # Let's test with the first one
            test_key = results[0]['key']
            print(f"\n🔬 Testing metadata retrieval with key: {test_key}")
            
            metadata = syncer.get_zotero_metadata(test_key)
            if metadata:
                print(f"\n✅ Metadata found:")
                for key, value in metadata.items():
                    if value:  # Only show non-empty values
                        display_value = value[:80] + "..." if len(str(value)) > 80 else value
                        print(f"   {key}: {display_value}")
                
                return test_key, metadata
            else:
                print("❌ No metadata retrieved")
                return None, None
        else:
            print("❌ No items found")
            return None, None
            
    except Exception as e:
        print(f"Error: {e}")
        return None, None

def test_metadata_generation():
    """Test the metadata and tag generation"""
    test_key, metadata = find_any_zotero_items()
    
    if not metadata:
        return
    
    print(f"\n🏷️  Testing tag generation...")
    
    # Generate tags using the same logic as production script
    tags = []
    
    if metadata['type']:
        tags.append(metadata['type'])
    if metadata['publication']:
        tags.append(metadata['publication'])
    if metadata['year'] and metadata['year'].isdigit():
        year = int(metadata['year'])
        decade = (year // 10) * 10
        tags.append(f"{decade}s")
    
    # Test thematic tags
    content_text = f"{metadata['title']} {metadata['description']}".lower()
    
    # Economic terms
    econ_terms = ['economics', 'economic', 'market', 'trade', 'regulation', 'policy', 'financial', 'monetary', 'fiscal', 'capitalism', 'labor', 'employment']
    if any(term in content_text for term in econ_terms):
        tags.append('economics')
    
    # Historical periods
    historical_periods = {
        'civil war': 'Civil War',
        'world war': 'World War', 
        'great depression': 'Great Depression',
        'cold war': 'Cold War',
        'reconstruction': 'Reconstruction',
        'progressive era': 'Progressive Era',
        'new deal': 'New Deal'
    }
    for period_key, period_tag in historical_periods.items():
        if period_key in content_text:
            tags.append(period_tag)
    
    # Geographic regions
    geographic_terms = {
        'american': 'American',
        'united states': 'United States',
        'europe': 'European',
        'britain': 'British', 
        'england': 'British',
        'france': 'French',
        'germany': 'German',
        'california': 'California',
        'south': 'American South'
    }
    for geo_key, geo_tag in geographic_terms.items():
        if geo_key in content_text:
            tags.append(geo_tag)
    
    # Social themes
    social_themes = ['race', 'gender', 'class', 'immigration', 'religion', 'education', 'urban', 'rural']
    for theme in social_themes:
        if theme in content_text:
            tags.append(theme.title())
    
    # Remove duplicates
    seen = set()
    tags = [tag for tag in tags if not (tag in seen or seen.add(tag))]
    
    print(f"Generated tags: {', '.join(tags)}")
    
    print(f"\n🎯 This demonstrates the tagging system working!")
    print(f"   The metadata sync would apply these tags to DEVONthink")
    print(f"   and set the native macOS metadata fields")
    
    return test_key, metadata, tags

if __name__ == "__main__":
    test_metadata_generation()