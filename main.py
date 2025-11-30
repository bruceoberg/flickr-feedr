#!/usr/bin/env python3
"""
Flickr to Apple Photos direct import.
Imports Flickr photos with metadata directly into a Photos library,
preserving multi-album membership.

Two-phase operation:
1. prep   - Analyze Flickr export and create action plan YAML
2. import - Execute action plan and import to Photos
"""

import json
import shutil
import sys
import tempfile
import yaml
from pathlib import Path
from typing import Dict, List, Set, Optional

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


# ============================================================================
# Configuration
# ============================================================================

pathDirFlickrData = Path('./flickr_export')
pathDirStaging = Path('./flickr_staged')
strLibraryNameExpected = 'Photos'


# ============================================================================
# Helper Functions
# ============================================================================

def ObjLoadJson(pathJson: Path) -> Dict:
    """Load and parse a JSON file."""
    with open(pathJson, 'r', encoding='utf-8') as fileJson:
        return json.load(fileJson)


def StrIdFromStrFile(strFile: str) -> Optional[str]:
    """
    Extract Flickr photo ID from filename.
    Flickr format: img_NNNN_PHOTOID_o.jpg
    Since 2020, uses 10-11 digit IDs.
    """
    lStrParts = strFile.rsplit('_', 2)
    if len(lStrParts) >= 2:
        return lStrParts[-2]
    return None


def MpStrIdObjMeta(pathDirFlickrData: Path) -> Dict[str, Dict]:
    """
    Build complete metadata map for all photos.
    Returns: {photo_id: {'albums': [...], 'json_path': '...', 'photo_path': '...'}}
    """
    # First, build album membership map
    pathAlbumsJson = pathDirFlickrData / 'albums.json'
    if not pathAlbumsJson.exists():
        print(f"Warning: albums.json not found, photos will have no album assignments", file=sys.stderr)
        lObjAlbum = {'albums': []}
    else:
        lObjAlbum = ObjLoadJson(pathAlbumsJson)
    
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
    
    for pathFile in pathDirFlickrData.iterdir():
        if not pathFile.is_file():
            continue
        
        if pathFile.suffix.lower() not in lStrPhotoExts:
            continue
        
        strPhotoId = StrIdFromStrFile(pathFile.name)
        if not strPhotoId:
            continue
        
        # Initialize if not in albums
        if strPhotoId not in mpStrIdObjMeta:
            mpStrIdObjMeta[strPhotoId] = {'albums': [], 'json_path': None, 'photo_path': None}
        
        # Store photo path
        mpStrIdObjMeta[strPhotoId]['photo_path'] = pathFile
        
        # Find corresponding JSON
        pathJson = pathDirFlickrData / f"photo_{strPhotoId}_o.json"
        
        if not pathJson.exists():
            # Try without _o suffix
            pathJson = pathDirFlickrData / f"photo_{strPhotoId}.json"
        
        if pathJson.exists():
            mpStrIdObjMeta[strPhotoId]['json_path'] = pathJson
    
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


def PrepareActionPlan():
    """
    Prepare import action plan by creating staged files with embedded metadata.
    Uses global configuration for paths.
    """
    print(f"Flickr data directory: {pathDirFlickrData}")
    print(f"Staging directory: {pathDirStaging}")
    
    if not pathDirFlickrData.is_dir():
        print(f"Error: {pathDirFlickrData} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    print("\nBuilding photo metadata map...")
    mpStrIdObjMeta = MpStrIdObjMeta(pathDirFlickrData)
    
    cPhotoTotal = len(mpStrIdObjMeta)
    print(f"Found {cPhotoTotal} photos to process")
    
    if cPhotoTotal == 0:
        print("No photos found!")
        return
    
    # Create staging directory
    pathDirStaging.mkdir(parents=True, exist_ok=True)
    
    # Prepare action plan
    objPlan = {
        'metadata': {
            'source_directory': str(pathDirFlickrData),
            'staging_directory': str(pathDirStaging),
            'total_photos': cPhotoTotal,
        },
        'albums': {},
        'actions': []
    }
    
    cPhotoProcessed = 0
    cPhotoWithMetadata = 0
    
    print("\nPreparing staged files with metadata...")
    
    with exiftool.ExifToolHelper() as etool:
        for strPhotoId, objMeta in mpStrIdObjMeta.items():
            pathPhotoSrc = objMeta.get('photo_path')
            pathJson = objMeta.get('json_path')
            lStrAlbums = objMeta.get('albums', [])
            
            if not pathPhotoSrc:
                print(f"Skipping photo {strPhotoId}: no photo file found", file=sys.stderr)
                continue
            
            # Create staged filename
            pathStaged = pathDirStaging / pathPhotoSrc.name
            
            # Copy to staging
            shutil.copy2(pathPhotoSrc, pathStaged)
            
            # Embed metadata if JSON exists
            fHasMetadata = False
            if pathJson:
                try:
                    objFlickrMeta = ObjLoadJson(pathJson)
                    objExif = ObjExifFromObjMeta(objFlickrMeta)
                    
                    if objExif:
                        etool.set_tags(
                            str(pathStaged),
                            objExif,
                            params=['-overwrite_original']
                        )
                        fHasMetadata = True
                        cPhotoWithMetadata += 1
                except Exception as err:
                    print(f"Warning: Failed to embed metadata for {pathPhotoSrc.name}: {err}", file=sys.stderr)
            
            # Build action entry
            objAction = {
                'photo_id': strPhotoId,
                'source_file': str(pathPhotoSrc),
                'staged_file': str(pathStaged),
                'filename': pathPhotoSrc.name,
                'has_metadata': fHasMetadata,
                'albums': lStrAlbums,
            }
            
            objPlan['actions'].append(objAction)
            
            # Track unique albums
            for strAlbumName in lStrAlbums:
                if strAlbumName not in objPlan['albums']:
                    objPlan['albums'][strAlbumName] = {
                        'name': strAlbumName,
                        'photo_count': 0
                    }
                objPlan['albums'][strAlbumName]['photo_count'] += 1
            
            cPhotoProcessed += 1
            if cPhotoProcessed % 100 == 0:
                print(f"Progress: {cPhotoProcessed}/{cPhotoTotal} prepared")
    
    # Update metadata
    objPlan['metadata']['photos_prepared'] = cPhotoProcessed
    objPlan['metadata']['photos_with_metadata'] = cPhotoWithMetadata
    objPlan['metadata']['album_count'] = len(objPlan['albums'])
    
    # Write YAML to staging directory
    pathYaml = pathDirStaging / 'import_plan.yaml'
    print(f"\nWriting action plan to {pathYaml}")
    with open(pathYaml, 'w') as fileYaml:
        yaml.dump(objPlan, fileYaml, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    print(f"\n{'='*60}")
    print(f"Preparation complete!")
    print(f"{'='*60}")
    print(f"Photos prepared:          {cPhotoProcessed}")
    print(f"Photos with metadata:     {cPhotoWithMetadata}")
    print(f"Unique albums:            {len(objPlan['albums'])}")
    print(f"\nNext step: Run 'import' command:")
    print(f"  python main.py import")


def AlbumEnsure(libPhotos: photoscript.PhotosLibrary, strAlbumName: str,
                mpAlbumCache: Dict[str, photoscript.Album]) -> photoscript.Album:
    """
    Get existing album or create new one. Uses cache to avoid repeated lookups.
    """
    if strAlbumName in mpAlbumCache:
        return mpAlbumCache[strAlbumName]
    
    # Try to get existing album
    album = libPhotos.album(strAlbumName)
    if album:
        mpAlbumCache[strAlbumName] = album
        return album
    
    # Album doesn't exist, create it
    try:
        album = libPhotos.create_album(strAlbumName)
        mpAlbumCache[strAlbumName] = album
        return album
    except Exception as err:
        print(f"Error creating album {strAlbumName}: {err}", file=sys.stderr)
        raise


def ExecuteActionPlan(iActionStart: int = 0):
    """
    Execute import action plan from YAML file in staging directory.
    
    Args:
        iActionStart: Index of action to start from (for resuming)
    """
    pathYaml = pathDirStaging / 'import_plan.yaml'
    
    if not pathYaml.exists():
        print(f"Error: Action plan not found at {pathYaml}", file=sys.stderr)
        print(f"Run 'prep' command first to create action plan.", file=sys.stderr)
        sys.exit(1)
    
    print(f"Loading action plan from {pathYaml}")
    
    with open(pathYaml, 'r') as fileYaml:
        objPlan = yaml.safe_load(fileYaml)
    
    objMetadata = objPlan['metadata']
    lActions = objPlan['actions']
    mpAlbums = objPlan['albums']
    
    cPhotoTotal = len(lActions)
    
    print(f"\nAction Plan Summary:")
    print(f"  Total photos:     {cPhotoTotal}")
    print(f"  Unique albums:    {len(mpAlbums)}")
    print(f"  Starting from:    Action #{iActionStart}")
    
    # Open Photos library
    print("\nOpening Photos library...")
    libPhotos = photoscript.PhotosLibrary()
    
    print(f"Connected to Photos library: {libPhotos.name}")
    print(f"Library version: {libPhotos.version}")
    
    # Verify library name
    strLibraryNameCurrent = libPhotos.name
    if strLibraryNameCurrent.endswith('.photoslibrary'):
        strLibraryNameCurrent = strLibraryNameCurrent[:-14]
    
    if strLibraryNameCurrent != strLibraryNameExpected:
        print(f"\nError: Expected library '{strLibraryNameExpected}' but found '{strLibraryNameCurrent}'", file=sys.stderr)
        print(f"Please open the correct library in Photos and try again.", file=sys.stderr)
        sys.exit(1)
    
    print(f"✓ Verified correct library: {strLibraryNameExpected}")
    
    # Cache for album objects
    mpAlbumCache = {}
    
    # Statistics
    cPhotoImported = 0
    cPhotoSkipped = 0
    cPhotoError = 0
    
    # Resume log path
    pathResumeLog = pathDirStaging / 'import_resume.txt'
    
    print(f"\n{'='*60}")
    print(f"Starting import...")
    print(f"{'='*60}\n")
    
    for iAction, objAction in enumerate(lActions[iActionStart:], start=iActionStart):
        strPhotoId = objAction['photo_id']
        pathStaged = Path(objAction['staged_file'])
        strFilename = objAction['filename']
        lStrAlbums = objAction['albums']
        
        if not pathStaged.exists():
            print(f"[{iAction + 1}/{cPhotoTotal}] ERROR: Staged file not found: {pathStaged}")
            cPhotoError += 1
            continue
        
        try:
            print(f"[{iAction + 1}/{cPhotoTotal}] Importing {strFilename}")
            
            # Import photo
            lPhotoImported = libPhotos.import_photos([str(pathStaged)], skip_duplicate_check=False)
            
            if not lPhotoImported:
                print(f"  └─ SKIPPED (duplicate or import failed)")
                cPhotoSkipped += 1
                
                # Log to resume file
                with open(pathResumeLog, 'a') as fileResume:
                    fileResume.write(f"{iAction}\t{strFilename}\tSKIPPED\n")
                
                continue
            
            assert len(lPhotoImported) == 1, f"Expected 1 imported photo, got {len(lPhotoImported)}"
            photoImported = lPhotoImported[0]
            cPhotoImported += 1
            
            # Add to albums
            if lStrAlbums:
                print(f"  └─ Adding to {len(lStrAlbums)} album(s): {', '.join(lStrAlbums)}")
                for strAlbumName in lStrAlbums:
                    try:
                        albumTarget = AlbumEnsure(libPhotos, strAlbumName, mpAlbumCache)
                        albumTarget.add([photoImported])
                    except Exception as err:
                        print(f"     ERROR adding to album {strAlbumName}: {err}", file=sys.stderr)
            
            # Log success
            with open(pathResumeLog, 'a') as fileResume:
                fileResume.write(f"{iAction}\t{strFilename}\tIMPORTED\n")
            
        except Exception as err:
            print(f"  └─ ERROR: {err}")
            cPhotoError += 1
            
            # Log error
            with open(pathResumeLog, 'a') as fileResume:
                fileResume.write(f"{iAction}\t{strFilename}\tERROR\t{err}\n")
            
            # Ask user if they want to continue
            print(f"\nImport error occurred at action #{iAction}.")
            print(f"To resume from this point, run:")
            print(f"  python main.py import --resume {iAction + 1}")
            
            strResponse = input("\nContinue importing? [y/N]: ").strip().lower()
            if strResponse != 'y':
                print("\nImport stopped by user.")
                break
    
    print(f"\n{'='*60}")
    print(f"Import complete!")
    print(f"{'='*60}")
    print(f"Photos imported:  {cPhotoImported}")
    print(f"Photos skipped:   {cPhotoSkipped}")
    print(f"Errors:           {cPhotoError}")
    print(f"\nSee '{pathResumeLog}' for detailed log.")


def main():
    """Entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Prepare: python main.py prep")
        print("  Import:  python main.py import [--resume N]")
        print("\nExamples:")
        print("  python main.py prep")
        print("  python main.py import")
        print("  python main.py import --resume 150")
        print("\nCommands:")
        print("  prep   - Analyze Flickr export and create staged files + action plan")
        print("  import - Execute action plan and import to Photos")
        print("\nConfiguration:")
        print(f"  Flickr data: {pathDirFlickrData}")
        print(f"  Staging dir: {pathDirStaging}")
        print(f"  Library:     {strLibraryNameExpected}")
        print("\nRequirements:")
        print("  - ExifTool must be installed")
        print("  - Python packages: pip install pyexiftool photoscript pyyaml")
        print("  - Photos.app must be running (for import)")
        sys.exit(1)
    
    strCommand = sys.argv[1]
    
    if strCommand == 'prep':
        PrepareActionPlan()
        
    elif strCommand == 'import':
        iActionStart = 0
        
        # Parse optional --resume argument
        for i in range(2, len(sys.argv)):
            if sys.argv[i] == '--resume':
                if i + 1 < len(sys.argv):
                    iActionStart = int(sys.argv[i + 1])
        
        ExecuteActionPlan(iActionStart)
        
    else:
        print(f"Error: Unknown command '{strCommand}'", file=sys.stderr)
        print("Valid commands: prep, import", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()