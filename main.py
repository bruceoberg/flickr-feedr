#!/usr/bin/env python3
"""
Flickr to Apple Photos direct import.
Imports Flickr photos with metadata directly into a Photos library,
preserving multi-album membership.
"""

import json
import os
import shutil
import sys
import tempfile
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


def ObjExifFromObjMeta(objMeta: Dict) -> Dict:
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


def StrPathTempWithExif(etool: exiftool.ExifToolHelper, strPathPhotoSrc: str,
                        strPathJson: str) -> str:
    """
    Create temp file with embedded EXIF metadata from Flickr JSON.
    
    Returns:
        Path to temp file with metadata, or None if failed.
        Caller is responsible for deleting temp file.
    """
    try:
        objMeta = ObjLoadJson(strPathJson)
        objExif = ObjExifFromObjMeta(objMeta)
        
        if not objExif:
            # No metadata to embed, just return source path
            return strPathPhotoSrc
        
        # Create temp file with same extension as source
        _, strExt = os.path.splitext(strPathPhotoSrc)
        fdTemp, strPathTemp = tempfile.mkstemp(suffix=strExt, prefix='flickr_')
        os.close(fdTemp)  # Close file descriptor, we'll use the path
        
        # Copy source to temp
        shutil.copy2(strPathPhotoSrc, strPathTemp)
        
        # Embed metadata into temp file
        etool.set_tags(
            strPathTemp,
            objExif,
            params=['-overwrite_original']
        )
        
        return strPathTemp
        
    except Exception as err:
        print(f"Error creating temp file with metadata for {strPathPhotoSrc}: {err}", file=sys.stderr)
        return None


def AlbumEnsure(libPhotos: photoscript.PhotosLibrary, strAlbumName: str,
                mpAlbumCache: Dict[str, photoscript.Album]) -> photoscript.Album:
    """
    Get existing album or create new one. Uses cache to avoid repeated lookups.
    """

    print(f"Ensuring album {strAlbumName}")

    if strAlbumName in mpAlbumCache:
        print(f"Found cached album {strAlbumName}")
        return mpAlbumCache[strAlbumName]
    
    album = libPhotos.album(strAlbumName)
    if album:
        print(f"Found app album {strAlbumName}: {album.uuid}")
    else:
        try:
            album = libPhotos.create_album(strAlbumName)
            print(f"Album created with id {album.uuid} and name {album.name}")
        except Exception as err:
            print(f"Error creating album {strAlbumName}: {err}", file=sys.stderr)
            exit(1)

    mpAlbumCache[strAlbumName] = album
    return album


def FVerifyLibraryName(libPhotos: photoscript.PhotosLibrary, strLibraryName: str) -> bool:
    """
    Verify that the currently open Photos library matches the expected name.
    
    Args:
        libPhotos: PhotosLibrary instance
        strLibraryName: Expected library name (without .photoslibrary extension)
    
    Returns:
        True if library names match, False otherwise
    """
    try:
        # Get the name of the currently open library
        strLibraryNameCurrent = libPhotos.name
        
        # Remove .photoslibrary extension if present in either name
        if strLibraryNameCurrent.endswith('.photoslibrary'):
            strLibraryNameCurrent = strLibraryNameCurrent[:-14]
        
        strLibraryNameExpected = strLibraryName
        if strLibraryNameExpected.endswith('.photoslibrary'):
            strLibraryNameExpected = strLibraryNameExpected[:-14]
        
        # Compare names (case-insensitive)
        return strLibraryNameCurrent.lower() == strLibraryNameExpected.lower()
        
    except Exception as err:
        print(f"Error verifying library name: {err}", file=sys.stderr)
        return False

def ImportFlickrToPhotos(strDirFlickrData: str, strLibraryName: str = None):
    """
    Import Flickr photos directly into Apple Photos library.
    
    Args:
        strDirFlickrData: Directory containing extracted Flickr export
        strLibraryName: Optional library name to verify (without .photoslibrary extension)
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
    libPhotos = photoscript.PhotosLibrary()
    
    print(f"Connected to Photos library: {libPhotos.name}")
    print(f"Library version: {libPhotos.version}")
    
    # Verify library name if provided
    if strLibraryName:
        if not FVerifyLibraryName(libPhotos, strLibraryName):
            print(f"\nError: Expected library '{strLibraryName}' but found '{libPhotos.name}'", file=sys.stderr)
            print(f"Please open the correct library in Photos and try again.", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Verified correct library: {strLibraryName}")

    # Cache for album objects
    mpAlbumCache = {}
    
    # Process photos
    cPhotoProcessed = 0
    cPhotoWithMetadata = 0
    cPhotoImported = 0
    
    with exiftool.ExifToolHelper() as etool:
        for strPhotoId, objMeta in mpStrIdObjMeta.items():
            strPathPhotoSrc = objMeta.get('photo_path')
            strPathJson = objMeta.get('json_path')
            lStrAlbums = objMeta.get('albums', [])
            
            if not strPathPhotoSrc:
                print(f"Skipping photo {strPhotoId}: no photo file found", file=sys.stderr)
                continue
            
            strPathPhotoToImport = None
            fUsedTemp = False
            
            try:
                # Step 1: Create temp file with EXIF metadata
                if strPathJson:
                    strPathPhotoTemp = StrPathTempWithExif(etool, strPathPhotoSrc, strPathJson)
                    if strPathPhotoTemp and strPathPhotoTemp != strPathPhotoSrc:
                        strPathPhotoToImport = strPathPhotoTemp
                        fUsedTemp = True
                        cPhotoWithMetadata += 1
                    elif strPathPhotoTemp == strPathPhotoSrc:
                        strPathPhotoToImport = strPathPhotoSrc
                    else:
                        # Failed to create temp, use source
                        strPathPhotoToImport = strPathPhotoSrc
                else:
                    strPathPhotoToImport = strPathPhotoSrc
                
                # Step 2: Import photo to Photos
                print(f"Importing {strPathPhotoToImport}")
                lPhotoImported = libPhotos.import_photos([strPathPhotoToImport], skip_duplicate_check=False)
                
                if not lPhotoImported:
                    print(f"Warning: Photo {strPathPhotoSrc} (and {strPathJson}/{strPathPhotoTemp}) was not imported (may be duplicate)", file=sys.stderr)
                    exit(1)

                assert(len(lPhotoImported)==1)
                photoImported = lPhotoImported[0]
                cPhotoImported += 1
                
                # Step 3: Add to all albums
                for strAlbumName in lStrAlbums:
                    try:
                        albumTarget = AlbumEnsure(libPhotos, strAlbumName, mpAlbumCache)
                        albumTarget.add([photoImported])
                    except Exception as err:
                        print(f"Error adding photo to album {strAlbumName}: {err}", file=sys.stderr)
                
            except Exception as err:
                print(f"Error importing {strPathPhotoSrc}: {err}", file=sys.stderr)
            finally:
                # Clean up temp file if we created one
                if fUsedTemp and strPathPhotoToImport and os.path.exists(strPathPhotoToImport):
                    try:
                        os.unlink(strPathPhotoToImport)
                    except Exception as err:
                        print(f"Warning: Failed to delete temp file {strPathPhotoToImport}: {err}", file=sys.stderr)
            
            cPhotoProcessed += 1
            if cPhotoProcessed % 50 == 0:
                print(f"Progress: {cPhotoProcessed}/{cPhotoTotal} processed, {cPhotoImported} imported")
    
    print(f"\nImport complete!")
    print(f"Total photos processed: {cPhotoProcessed}")
    print(f"Photos imported: {cPhotoImported}")
    print(f"Photos with metadata embedded: {cPhotoWithMetadata}")
    print(f"Unique albums created/used: {len(mpAlbumCache)}")


def main():
    """Entry point."""
    if len(sys.argv) < 2:
        print("Usage: python flickr_to_photos_direct.py <flickr_data_dir> [library_name]")
        print("\nExample:")
        print("  python flickr_to_photos_direct.py ./flickr_export")
        print("  python flickr_to_photos_direct.py ./flickr_export FlickrArchive")
        print("\nArguments:")
        print("  flickr_data_dir: Directory containing extracted Flickr export")
        print("  library_name: Optional - name of Photos library to verify (without .photoslibrary)")
        print("\nNotes:")
        print("  - Open the desired Photos library before running this script")
        print("  - If library_name is provided, script will verify it matches the open library")
        print("\nRequirements:")
        print("  - ExifTool must be installed")
        print("  - Python packages: pip install pyexiftool photoscript")
        print("  - Photos.app must be running")
        sys.exit(1)
    
    strDirFlickrData = sys.argv[1]
    strLibraryName = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.isdir(strDirFlickrData):
        print(f"Error: {strDirFlickrData} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    ImportFlickrToPhotos(strDirFlickrData, strLibraryName)

if __name__ == '__main__':
    main()