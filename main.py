#!/usr/bin/env python3
"""
Flickr to Apple Photos direct import.
Imports Flickr photos with metadata directly into a Photos library,
preserving multi-album membership.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set

try:
    import exiftool
except ImportError:
    print("Error: pyexiftool not installed. Install with: pip install pyexiftool", file=sys.stderr)
    sys.exit(1)

try:
    import photoscript
except ImportError:
    print("Error: photoscript not installed. Install with: pip install photoscript", file=sys.stderr)
    sys.exit(1)


def fnLoadJson(strPathJson: str) -> Dict:
    """Load and parse a JSON file."""
    with open(strPathJson, 'r', encoding='utf-8') as fileJson:
        return json.load(fileJson)


def fnExtractPhotoId(strFilename: str) -> str:
    """
    Extract Flickr photo ID from filename.
    Flickr format: img_NNNN_PHOTOID_o.jpg
    Since 2020, uses 10-11 digit IDs.
    """
    rgstrParts = strFilename.rsplit('_', 2)
    if len(rgstrParts) >= 2:
        return rgstrParts[-2]
    return None


def fnBuildPhotoMetadata(strDirFlickrData: str) -> Dict[str, Dict]:
    """
    Build complete metadata map for all photos.
    Returns: {photo_id: {'albums': [...], 'json_path': '...', 'photo_path': '...'}}
    """
    # First, build album membership map
    strPathAlbumsJson = os.path.join(strDirFlickrData, 'albums.json')
    if not os.path.exists(strPathAlbumsJson):
        print(f"Warning: albums.json not found, photos will have no album assignments", file=sys.stderr)
        dictAlbumsData = {'albums': []}
    else:
        dictAlbumsData = fnLoadJson(strPathAlbumsJson)
    
    mapPhotoMeta = {}
    
    # Build photo -> albums mapping
    for dictAlbum in dictAlbumsData.get('albums', []):
        strAlbumName = dictAlbum.get('title', 'Untitled')
        # Sanitize album name
        strAlbumName = "".join(c for c in strAlbumName if c.isalnum() or c in (' ', '-', '_')).strip()
        
        for strPhotoId in dictAlbum.get('photos', []):
            if strPhotoId not in mapPhotoMeta:
                mapPhotoMeta[strPhotoId] = {'albums': [], 'json_path': None, 'photo_path': None}
            mapPhotoMeta[strPhotoId]['albums'].append(strAlbumName)
    
    # Now find all photo files and their JSON metadata
    rgstrPhotoExts = {'.jpg', '.jpeg', '.png', '.gif', '.mov', '.mp4', '.avi'}
    
    for strFilename in os.listdir(strDirFlickrData):
        strPathFile = os.path.join(strDirFlickrData, strFilename)
        
        if not os.path.isfile(strPathFile):
            continue
        
        _, strExt = os.path.splitext(strFilename.lower())
        if strExt not in rgstrPhotoExts:
            continue
        
        strPhotoId = fnExtractPhotoId(strFilename)
        if not strPhotoId:
            continue
        
        # Initialize if not in albums
        if strPhotoId not in mapPhotoMeta:
            mapPhotoMeta[strPhotoId] = {'albums': [], 'json_path': None, 'photo_path': None}
        
        # Store photo path
        mapPhotoMeta[strPhotoId]['photo_path'] = strPathFile
        
        # Find corresponding JSON
        strJsonFilename = f"photo_{strPhotoId}_o.json"
        strPathJson = os.path.join(strDirFlickrData, strJsonFilename)
        
        if not os.path.exists(strPathJson):
            # Try without _o suffix
            strJsonFilename = f"photo_{strPhotoId}.json"
            strPathJson = os.path.join(strDirFlickrData, strJsonFilename)
        
        if os.path.exists(strPathJson):
            mapPhotoMeta[strPhotoId]['json_path'] = strPathJson
    
    return mapPhotoMeta


def fnBuildMetadataDict(dictPhotoMeta: Dict, rgstrAlbumNames: List[str]) -> Dict:
    """
    Build ExifTool metadata dictionary from Flickr JSON.
    Maps Flickr fields to IPTC/XMP fields that Apple Photos can read.
    """
    dictExifTags = {}
    
    # Title
    strTitle = dictPhotoMeta.get('name', '')
    if strTitle:
        dictExifTags['IPTC:ObjectName'] = strTitle
        dictExifTags['XMP-dc:Title'] = strTitle
    
    # Description
    strDescription = dictPhotoMeta.get('description', '')
    if strDescription:
        dictExifTags['IPTC:Caption-Abstract'] = strDescription
        dictExifTags['XMP-dc:Description'] = strDescription
    
    # Tags/Keywords - collect all tags
    rgstrTags = []
    for dictTag in dictPhotoMeta.get('tags', []):
        strTag = dictTag.get('tag', '')
        if strTag:
            rgstrTags.append(strTag)
    
    # Add album names as keywords
    rgstrTags.extend(rgstrAlbumNames)
    
    # IPTC:Keywords and XMP-dc:Subject can be lists
    if rgstrTags:
        dictExifTags['IPTC:Keywords'] = rgstrTags
        dictExifTags['XMP-dc:Subject'] = rgstrTags
    
    # Date taken
    strDateTaken = dictPhotoMeta.get('date_taken', '')
    if strDateTaken:
        dictExifTags['DateTimeOriginal'] = strDateTaken
    
    # GPS coordinates
    fLatitude = dictPhotoMeta.get('latitude')
    fLongitude = dictPhotoMeta.get('longitude')
    if fLatitude is not None and fLongitude is not None:
        dictExifTags['GPSLatitude*'] = fLatitude
        dictExifTags['GPSLongitude*'] = fLongitude
    
    # License/Copyright
    strLicense = dictPhotoMeta.get('license', '')
    if strLicense:
        dictExifTags['XMP-dc:Rights'] = strLicense
    
    return dictExifTags


def fnEmbedExifMetadata(etExifTool: exiftool.ExifToolHelper, strPathPhoto: str,
                        strPathJson: str, rgstrAlbumNames: List[str]) -> bool:
    """
    Embed Flickr JSON metadata into photo EXIF using ExifTool.
    """
    try:
        dictPhotoMeta = fnLoadJson(strPathJson)
        dictExifTags = fnBuildMetadataDict(dictPhotoMeta, rgstrAlbumNames)
        
        if dictExifTags:
            etExifTool.set_tags(
                strPathPhoto,
                dictExifTags,
                params=['-overwrite_original']
            )
        return True
    except Exception as err:
        print(f"Error embedding metadata for {strPathPhoto}: {err}", file=sys.stderr)
        return False


def fnGetOrCreateAlbum(libPhotos: photoscript.PhotosLibrary, strAlbumName: str,
                       mapAlbumCache: Dict[str, photoscript.Album]) -> photoscript.Album:
    """
    Get existing album or create new one. Uses cache to avoid repeated lookups.
    """
    if strAlbumName in mapAlbumCache:
        return mapAlbumCache[strAlbumName]
    
    try:
        album = libPhotos.album(strAlbumName)
        mapAlbumCache[strAlbumName] = album
        return album
    except:
        # Album doesn't exist, create it
        album = libPhotos.create_album(strAlbumName)
        mapAlbumCache[strAlbumName] = album
        return album


def fnImportFlickrToPhotos(strDirFlickrData: str, strPathLibrary: str = None):
    """
    Import Flickr photos directly into Apple Photos library.
    
    Args:
        strDirFlickrData: Directory containing extracted Flickr export
        strPathLibrary: Optional path to Photos library (uses last opened if None)
    """
    print("Building photo metadata map...")
    mapPhotoMeta = fnBuildPhotoMetadata(strDirFlickrData)
    
    nTotalPhotos = len(mapPhotoMeta)
    print(f"Found {nTotalPhotos} photos to import")
    
    if nTotalPhotos == 0:
        print("No photos found to import!")
        return
    
    # Open Photos library
    print("Opening Photos library...")
    if strPathLibrary:
        # photoscript doesn't support opening specific library directly
        # User must open the desired library before running this script
        print(f"Note: Make sure {strPathLibrary} is the currently open library in Photos")
    
    libPhotos = photoscript.PhotosLibrary()
    print(f"Connected to Photos library version {libPhotos.version}")
    
    # Cache for album objects
    mapAlbumCache = {}
    
    # Process photos
    nProcessed = 0
    nWithMetadata = 0
    nImported = 0
    
    with exiftool.ExifToolHelper() as etExifTool:
        for strPhotoId, dictMeta in mapPhotoMeta.items():
            strPathPhoto = dictMeta.get('photo_path')
            strPathJson = dictMeta.get('json_path')
            rgstrAlbums = dictMeta.get('albums', [])
            
            if not strPathPhoto:
                print(f"Skipping photo {strPhotoId}: no photo file found", file=sys.stderr)
                continue
            
            # Step 1: Embed EXIF metadata
            if strPathJson:
                if fnEmbedExifMetadata(etExifTool, strPathPhoto, strPathJson, rgstrAlbums):
                    nWithMetadata += 1
            
            # Step 2: Import photo to Photos
            try:
                rgPhotosImported = libPhotos.import_photos([strPathPhoto], skip_duplicate_check=False)
                
                if not rgPhotosImported:
                    print(f"Warning: Photo {strPathPhoto} was not imported (may be duplicate)", file=sys.stderr)
                    nProcessed += 1
                    continue
                
                photoImported = rgPhotosImported[0]
                nImported += 1
                
                # Step 3: Add to all albums
                for strAlbumName in rgstrAlbums:
                    try:
                        albumTarget = fnGetOrCreateAlbum(libPhotos, strAlbumName, mapAlbumCache)
                        albumTarget.add([photoImported])
                    except Exception as err:
                        print(f"Error adding photo to album {strAlbumName}: {err}", file=sys.stderr)
                
            except Exception as err:
                print(f"Error importing {strPathPhoto}: {err}", file=sys.stderr)
            
            nProcessed += 1
            if nProcessed % 50 == 0:
                print(f"Progress: {nProcessed}/{nTotalPhotos} processed, {nImported} imported")
    
    print(f"\nImport complete!")
    print(f"Total photos processed: {nProcessed}")
    print(f"Photos imported: {nImported}")
    print(f"Photos with metadata embedded: {nWithMetadata}")
    print(f"Unique albums created/used: {len(mapAlbumCache)}")


def main():
    """Entry point."""
    if len(sys.argv) < 2:
        print("Usage: python flickr_to_photos_direct.py <flickr_data_dir> [photos_library_path]")
        print("\nExample:")
        print("  python flickr_to_photos_direct.py ./flickr_export")
        print("  python flickr_to_photos_direct.py ./flickr_export ~/Pictures/MyLibrary.photoslibrary")
        print("\nNotes:")
        print("  - If library path is provided, open that library in Photos before running")
        print("  - Otherwise, the currently open Photos library will be used")
        print("\nRequirements:")
        print("  - ExifTool must be installed")
        print("  - Python packages: pip install pyexiftool photoscript")
        print("  - Photos.app must be running")
        sys.exit(1)
    
    strDirFlickrData = sys.argv[1]
    strPathLibrary = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.isdir(strDirFlickrData):
        print(f"Error: {strDirFlickrData} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    if strPathLibrary and not os.path.exists(strPathLibrary):
        print(f"Warning: {strPathLibrary} does not exist", file=sys.stderr)
    
    fnImportFlickrToPhotos(strDirFlickrData, strPathLibrary)


if __name__ == '__main__':
    main()
