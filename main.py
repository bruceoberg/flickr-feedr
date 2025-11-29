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


def ObjLoadJson(strPathJson: str) -> Dict:
    """Load and parse a JSON file."""
    with open(strPathJson, 'r', encoding='utf-8') as fileJson:
        return json.load(fileJson)


def StrIdFromStrFile(strFile: str) -> str:
    """
    Extract Flickr photo ID from filename.
    Flickr format: img_NNNN_PHOTOID_o.jpg
    Since 2020, uses 10-11 digit IDs.
    """
    lStrParts = strFile.rsplit('_', 2)
    if len(lStrParts) >= 2:
        return lStrParts[-2]
    return None


def MpStrIdObjMeta(strDirFlickrData: str) -> Dict[str, Dict]:
    """
    Build complete metadata map for all photos.
    Returns: {photo_id: {'albums': [...], 'json_path': '...', 'photo_path': '...'}}
    """
    # First, build album membership map
    strPathAlbumsJson = os.path.join(strDirFlickrData, 'albums.json')
    if not os.path.exists(strPathAlbumsJson):
        print(f"Warning: albums.json not found, photos will have no album assignments", file=sys.stderr)
        lObjAlbum = {'albums': []}
    else:
        lObjAlbum = ObjLoadJson(strPathAlbumsJson)
    
    mpStrIdObjMeta = {}
    
    # Build photo -> albums mapping
    for objAlbum in lObjAlbum.get('albums', []):
        strAlbumName = objAlbum.get('title', 'Untitled')
        # Sanitize album name
        strAlbumName = "".join(c for c in strAlbumName if c.isalnum() or c in (' ', '-', '_')).strip()
        
        for strPhotoId in objAlbum.get('photos', []):
            if strPhotoId not in mpStrIdObjMeta:
                mpStrIdObjMeta[strPhotoId] = {'albums': [], 'json_path': None, 'photo_path': None}
            mpStrIdObjMeta[strPhotoId]['albums'].append(strAlbumName)
    
    # Now find all photo files and their JSON metadata
    lStrPhotoExts = {'.jpg', '.jpeg', '.png', '.gif', '.mov', '.mp4', '.avi'}
    
    for strFilename in os.listdir(strDirFlickrData):
        strPathFile = os.path.join(strDirFlickrData, strFilename)
        
        if not os.path.isfile(strPathFile):
            continue
        
        _, strExt = os.path.splitext(strFilename.lower())
        if strExt not in lStrPhotoExts:
            continue
        
        strPhotoId = StrIdFromStrFile(strFilename)
        if not strPhotoId:
            continue
        
        # Initialize if not in albums
        if strPhotoId not in mpStrIdObjMeta:
            mpStrIdObjMeta[strPhotoId] = {'albums': [], 'json_path': None, 'photo_path': None}
        
        # Store photo path
        mpStrIdObjMeta[strPhotoId]['photo_path'] = strPathFile
        
        # Find corresponding JSON
        strJsonFilename = f"photo_{strPhotoId}_o.json"
        strPathJson = os.path.join(strDirFlickrData, strJsonFilename)
        
        if not os.path.exists(strPathJson):
            # Try without _o suffix
            strJsonFilename = f"photo_{strPhotoId}.json"
            strPathJson = os.path.join(strDirFlickrData, strJsonFilename)
        
        if os.path.exists(strPathJson):
            mpStrIdObjMeta[strPhotoId]['json_path'] = strPathJson
    
    return mpStrIdObjMeta


def ObjExifFromObjMeta(objMeta: Dict, lStrAlbumNames: List[str]) -> Dict:
    """
    Build ExifTool metadata dictionary from Flickr JSON.
    Maps Flickr fields to IPTC/XMP fields that Apple Photos can read.
    """
    objExif = {}
    
    # Title
    strTitle = objMeta.get('name', '')
    if strTitle:
        objExif['IPTC:ObjectName'] = strTitle
        objExif['XMP-dc:Title'] = strTitle
    
    # Description
    strDescription = objMeta.get('description', '')
    if strDescription:
        objExif['IPTC:Caption-Abstract'] = strDescription
        objExif['XMP-dc:Description'] = strDescription
    
    # Tags/Keywords - collect all tags
    lStrTags = []
    for dictTag in objMeta.get('tags', []):
        strTag = dictTag.get('tag', '')
        if strTag:
            lStrTags.append(strTag)
    
    # Add album names as keywords
    lStrTags.extend(lStrAlbumNames)
    
    # IPTC:Keywords and XMP-dc:Subject can be lists
    if lStrTags:
        objExif['IPTC:Keywords'] = lStrTags
        objExif['XMP-dc:Subject'] = lStrTags
    
    # Date taken
    strDateTaken = objMeta.get('date_taken', '')
    if strDateTaken:
        objExif['DateTimeOriginal'] = strDateTaken
    
    # GPS coordinates
    fLatitude = objMeta.get('latitude')
    fLongitude = objMeta.get('longitude')
    if fLatitude is not None and fLongitude is not None:
        objExif['GPSLatitude*'] = fLatitude
        objExif['GPSLongitude*'] = fLongitude
    
    # License/Copyright
    strLicense = objMeta.get('license', '')
    if strLicense:
        objExif['XMP-dc:Rights'] = strLicense
    
    return objExif


def FEmbedExifMetadata(etool: exiftool.ExifToolHelper, strPathPhoto: str,
                        strPathJson: str, lStrAlbumNames: List[str]) -> bool:
    """
    Embed Flickr JSON metadata into photo EXIF using ExifTool.
    """
    try:
        objMeta = ObjLoadJson(strPathJson)
        objExif = ObjExifFromObjMeta(objMeta, lStrAlbumNames)
        
        if objExif:
            etool.set_tags(
                strPathPhoto,
                objExif,
                params=['-overwrite_original']
            )
        return True
    except Exception as err:
        print(f"Error embedding metadata for {strPathPhoto}: {err}", file=sys.stderr)
        return False


def AlbumEnsure(libPhotos: photoscript.PhotosLibrary, strAlbumName: str,
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


def ImportFlickrToPhotos(strDirFlickrData: str, strPathLibrary: str = None):
    """
    Import Flickr photos directly into Apple Photos library.
    
    Args:
        strDirFlickrData: Directory containing extracted Flickr export
        strPathLibrary: Optional path to Photos library (uses last opened if None)
    """
    print("Building photo metadata map...")
    mpStrIdObjMeta = MpStrIdObjMeta(strDirFlickrData)
    
    cPhotoTotal = len(mpStrIdObjMeta)
    print(f"Found {cPhotoTotal} photos to import")
    
    if cPhotoTotal == 0:
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
    cPhotoProcessed = 0
    cPhotoWithMetadata = 0
    cPhotoImported = 0
    
    with exiftool.ExifToolHelper() as etool:
        for strPhotoId, objMeta in mpStrIdObjMeta.items():
            strPathPhoto = objMeta.get('photo_path')
            strPathJson = objMeta.get('json_path')
            lStrAlbum = objMeta.get('albums', [])
            
            if not strPathPhoto:
                print(f"Skipping photo {strPhotoId}: no photo file found", file=sys.stderr)
                continue
            
            # Step 1: Embed EXIF metadata
            if strPathJson:
                if FEmbedExifMetadata(etool, strPathPhoto, strPathJson, lStrAlbum):
                    cPhotoWithMetadata += 1
            
            # Step 2: Import photo to Photos
            try:
                lPhotoImported = libPhotos.import_photos([strPathPhoto], skip_duplicate_check=False)
                
                if not lPhotoImported:
                    print(f"Warning: Photo {strPathPhoto} was not imported (may be duplicate)", file=sys.stderr)
                    cPhotoProcessed += 1
                    continue
                
                photoImported = lPhotoImported[0]
                cPhotoImported += 1
                
                # Step 3: Add to all albums
                for strAlbumName in lStrAlbum:
                    try:
                        albumTarget = AlbumEnsure(libPhotos, strAlbumName, mapAlbumCache)
                        albumTarget.add([photoImported])
                    except Exception as err:
                        print(f"Error adding photo to album {strAlbumName}: {err}", file=sys.stderr)
                
            except Exception as err:
                print(f"Error importing {strPathPhoto}: {err}", file=sys.stderr)
            
            cPhotoProcessed += 1
            if cPhotoProcessed % 50 == 0:
                print(f"Progress: {cPhotoProcessed}/{cPhotoTotal} processed, {cPhotoImported} imported")
    
    print(f"\nImport complete!")
    print(f"Total photos processed: {cPhotoProcessed}")
    print(f"Photos imported: {cPhotoImported}")
    print(f"Photos with metadata embedded: {cPhotoWithMetadata}")
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
    
    ImportFlickrToPhotos(strDirFlickrData, strPathLibrary)

if __name__ == '__main__':
    main()
