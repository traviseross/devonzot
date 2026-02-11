#!/usr/bin/env python3
"""
Direct test of the tagging and metadata system with known data
"""

import sys
sys.path.append('/Users/travisross/DEVONzot')
from production_metadata_sync import ZoteroDevonthinkMetadataSync
import os

def test_with_sample_data():
    """Test the metadata and tagging system with sample data"""
    
    # Sample metadata that matches our Henderson article
    sample_metadata = {
        'title': 'How Black Is Our Market?',
        'author': 'Leon Henderson',
        'publication': 'The Atlantic',
        'year': '1946',
        'type': 'Magazine Article',
        'description': 'Article about market economics and competition in post-war America, discussing regulation and free market principles.'
    }
    
    print("🧪 Testing Metadata & Tagging System")
    print("=" * 50)
    print("📄 Sample metadata:")
    for key, value in sample_metadata.items():
        print(f"   {key}: {value}")
    
    # Test tag generation using production script logic
    print(f"\n🏷️  Generating smart tags...")
    tags = []
    
    # Add exact Zotero item type as tag
    if sample_metadata['type']:
        tags.append(sample_metadata['type'])
    
    # Add publication name
    if sample_metadata['publication']:
        tags.append(sample_metadata['publication'])
    
    # Add decade tag
    if sample_metadata['year'] and sample_metadata['year'].isdigit():
        year = int(sample_metadata['year'])
        decade = (year // 10) * 10
        tags.append(f"{decade}s")
    
    # Extract thematic tags
    content_text = f"{sample_metadata['title']} {sample_metadata['description']}".lower()
    
    # Economic/political terms
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
        'new deal': 'New Deal',
        'post-war': 'Post-War'
    }
    for period_key, period_tag in historical_periods.items():
        if period_key in content_text:
            tags.append(period_tag)
    
    # Geographic regions
    geographic_terms = {
        'american': 'American',
        'america': 'American',
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
    
    # Social/cultural themes
    social_themes = ['race', 'gender', 'class', 'immigration', 'religion', 'education', 'urban', 'rural', 'competition']
    for theme in social_themes:
        if theme in content_text:
            tags.append(theme.title())
    
    # Remove duplicates while preserving order
    seen = set()
    tags = [tag for tag in tags if not (tag in seen or seen.add(tag))]
    
    print(f"Generated tags: {', '.join(tags)}")
    
    # Test the actual sync functions
    syncer = ZoteroDevonthinkMetadataSync()
    test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    test_file_path = "/Users/travisross/DEVONthink/Articles.dtBase2/Files.noindex/pdf/19/Henderson - How Black Is Our Market - The Atlantic - 1946 - Magazine Article.pdf"
    
    print(f"\n🎯 Ready to apply to DEVONthink record:")
    print(f"   UUID: {test_uuid}")
    print(f"   File: {os.path.basename(test_file_path)}")
    print(f"   Tags to apply: {', '.join(tags)}")
    print(f"   Metadata: Title, Author, Description")
    
    response = input(f"\nApply this sync? (y/n): ").lower().strip()
    
    if response == 'y':
        print(f"\n🚀 Applying metadata sync...")
        
        # Set macOS metadata
        if os.path.exists(test_file_path):
            print("📝 Setting macOS metadata...")
            macos_success = syncer.set_macos_metadata(test_file_path, sample_metadata)
            print(f"   macOS metadata: {'✅ Success' if macos_success else '❌ Failed'}")
        else:
            print("⚠️  File not found, skipping macOS metadata")
            macos_success = False
        
        # Set DEVONthink tags
        print("🏷️  Setting DEVONthink tags...")
        dt_success = syncer.set_devonthink_tags(test_uuid, tags)
        print(f"   DEVONthink tags: {'✅ Success' if dt_success else '❌ Failed'}")
        
        if dt_success or macos_success:
            print(f"\n🎉 Metadata sync completed!")
            print(f"🔗 Check results: x-devonthink-item://{test_uuid}")
            print(f"\n📋 What was applied:")
            print(f"   • Tags: {', '.join(tags)}")
            if macos_success:
                print(f"   • macOS Author: {sample_metadata['author']}")
                print(f"   • macOS Title: {sample_metadata['title']}")
                print(f"   • macOS Description: {sample_metadata['description']}")
            print(f"\n✨ This demonstrates the full metadata sync system working!")
        else:
            print(f"❌ Sync failed")
    else:
        print(f"🚫 Test cancelled")
        print(f"📝 The system is ready to work - this shows what it would do!")

if __name__ == "__main__":
    test_with_sample_data()