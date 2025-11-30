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
import yaml
from pathlib import Path
from typing import Dict, List, Set, Optional

try:
    import photoscript
except ImportError:
    print("Error: photoscript not installed. Install with: pip install photoscript", file=sys.stderr)
    sys.exit(1)


# ============================================================================
# Configuration
# ============================================================================

pathDirFeedr = Path('/Users/bruceoberg/Downloads/feedr')
pathDirFlickr = pathDirFeedr / 'flickr'
pathDirExport = pathDirFlickr / 'unzipped'
pathDirStaging = pathDirFlickr / 'stage'
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
            if strPhotoId == 0 or strPhotoId == '0':
                continue
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
            if strPhotoId == 0 or strPhotoId == '0':
                continue
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


def ObjMetadataFromFlickrJson(pathJson: Path) -> Dict:
    """
    Extract metadata from Flickr JSON for use with photoscript.
    Returns dict with: title, description, keywords, latitude, longitude
    """
    objFlickrMeta = ObjLoadJson(pathJson)
    objMeta = {}
    
    # Title
    strTitle = objFlickrMeta.get('name', '')
    if strTitle:
        objMeta['title'] = strTitle
    
    # Description
    strDescription = objFlickrMeta.get('description', '')
    if strDescription:
        objMeta['description'] = strDescription
    
    # Tags/Keywords - collect all tags
    lStrTags = []
    for dictTag in objFlickrMeta.get('tags', []):
        strTag = dictTag.get('tag', '')
        if strTag:
            lStrTags.append(strTag)
    
    if lStrTags:
        objMeta['keywords'] = lStrTags
    
    # GPS coordinates - Flickr stores as strings multiplied by 1,000,000
    # Format: "geo": [{"latitude": "39091133", "longitude": "-94426535", ...}]
    lGeo = objFlickrMeta.get('geo', [])
    if lGeo and len(lGeo) > 0:
        objGeo = lGeo[0]
        strLatitude = objGeo.get('latitude')
        strLongitude = objGeo.get('longitude')
        
        if strLatitude and strLongitude:
            try:
                # Convert from Flickr's format (multiplied by 1,000,000) to decimal degrees
                fLatitude = float(strLatitude) / 1000000.0
                fLongitude = float(strLongitude) / 1000000.0
                
                # Validate ranges: latitude -90 to 90, longitude -180 to 180
                if -90 <= fLatitude <= 90 and -180 <= fLongitude <= 180:
                    objMeta['latitude'] = fLatitude
                    objMeta['longitude'] = fLongitude
                else:
                    print(f"Warning: Coordinates out of range: lat={fLatitude}, lon={fLongitude}", file=sys.stderr)
            except (ValueError, TypeError) as err:
                print(f"Warning: Failed to parse coordinates: {err}", file=sys.stderr)
    
    return objMeta


def PrepareActionPlan():
    """
    Prepare import action plan by copying files to staging and extracting metadata.
    Uses global configuration for paths.
    """
    print(f"Flickr data directory: {pathDirExport}")
    print(f"Staging directory: {pathDirStaging}")
    
    if not pathDirExport.is_dir():
        print(f"Error: {pathDirExport} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    print("\nBuilding photo metadata map...")
    mpStrIdObjMeta = MpStrIdObjMeta(pathDirExport)
    
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
            'source_directory': str(pathDirExport),
            'staging_directory': str(pathDirStaging),
            'total_photos': cPhotoTotal,
        },
        'albums': {},
        'actions': []
    }
    
    cPhotoProcessed = 0
    cPhotoWithMetadata = 0
    
    print("\nPreparing staged files...")
    
    for strPhotoId, objMeta in mpStrIdObjMeta.items():
        pathPhotoSrc = objMeta.get('photo_path')
        pathJson = objMeta.get('json_path')
        lStrAlbums = objMeta.get('albums', [])
        
        if not pathPhotoSrc:
            print(f"Skipping photo {strPhotoId}: no photo file found", file=sys.stderr)
            print(f"  objMeta: {objMeta}")
            continue
        
        # Create staged filename - just copy original file
        pathStaged = pathDirStaging / pathPhotoSrc.name
        
        # Copy to staging
        shutil.copy2(pathPhotoSrc, pathStaged)
        
        # Extract metadata from JSON if exists
        objFlickrMeta = None
        if pathJson:
            try:
                objFlickrMeta = ObjMetadataFromFlickrJson(pathJson)
                if objFlickrMeta:
                    cPhotoWithMetadata += 1
            except Exception as err:
                print(f"Warning: Failed to extract metadata for {pathPhotoSrc.name}: {err}", file=sys.stderr)
        
        # Build action entry
        objAction = {
            'photo_id': strPhotoId,
            'source_file': str(pathPhotoSrc),
            'staged_file': str(pathStaged),
            'filename': pathPhotoSrc.name,
            'albums': lStrAlbums,
        }
        
        # Add metadata if available
        if objFlickrMeta:
            objAction['metadata'] = objFlickrMeta
        
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


def ApplyMetadataToPhoto(photo: photoscript.Photo, objMeta: Dict):
    """
    Apply Flickr metadata to imported photo using photoscript APIs.
    
    Args:
        photo: PhotoScript Photo object
        objMeta: Metadata dict with title, description, keywords, latitude, longitude
    """
    try:
        # Set title
        if 'title' in objMeta:
            photo.title = objMeta['title']
        
        # Set description
        if 'description' in objMeta:
            photo.description = objMeta['description']
        
        # Set keywords
        if 'keywords' in objMeta:
            photo.keywords = objMeta['keywords']
        
        # Set location
        if 'latitude' in objMeta and 'longitude' in objMeta:
            photo.location = (objMeta['latitude'], objMeta['longitude'])
            
    except Exception as err:
        print(f"     Warning: Failed to apply some metadata: {err}", file=sys.stderr)


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
    cPhotoMetadataApplied = 0
    
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
        objMeta = objAction.get('metadata')
        
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
            
            # Apply metadata using photoscript APIs
            if objMeta:
                print(f"  └─ Applying metadata (title, description, keywords, location)")
                ApplyMetadataToPhoto(photoImported, objMeta)
                cPhotoMetadataApplied += 1
            
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
    print(f"Photos imported:       {cPhotoImported}")
    print(f"Metadata applied:      {cPhotoMetadataApplied}")
    print(f"Photos skipped:        {cPhotoSkipped}")
    print(f"Errors:                {cPhotoError}")
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
        print(f"  Flickr data: {pathDirExport}")
        print(f"  Staging dir: {pathDirStaging}")
        print(f"  Library:     {strLibraryNameExpected}")
        print("\nRequirements:")
        print("  - Python packages: pip install photoscript pyyaml")
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