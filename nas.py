#!/usr/bin/env python3
"""
Import photos from NAS directory structure into Apple Photos,
creating nested folders and albums matching the source structure.
"""

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

pathDirSource = Path('/Volumes/storage/media/Pictures')
pathDirStaging = Path('./nas_stage')
strLibraryNameExpected = 'Photos'

setStrPhotoExts = {'.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif', '.tiff', '.tif', '.bmp'}
setStrVideoExts = {'.mov', '.mp4', '.avi', '.m4v', '.mkv'}


# ============================================================================
# Helper Functions
# ============================================================================

def LPathFileInDir(pathDir: Path, pathDirStaging: Path) -> tuple[List[Path], List[Path]]:
    """
    Get all files in directory recursively.
    
    Returns:
        Tuple of (list of photo/video files, list of unhandled files)
    """
    lPathMedia = []
    lPathUnhandled = []
    setStrValidExts = setStrPhotoExts | setStrVideoExts
    
    for pathFile in pathDir.rglob('*'):
        # Skip staging directory
        if pathDirStaging in pathFile.parents or pathFile == pathDirStaging:
            continue
        
        if pathFile.is_file():
            if pathFile.suffix.lower() in setStrValidExts:
                lPathMedia.append(pathFile)
            else:
                # Skip hidden files and common non-media files
                if not pathFile.name.startswith('.'):
                    lPathUnhandled.append(pathFile)
    
    return lPathMedia, lPathUnhandled


def ObjAlbumInfoFromPath(pathFile: Path, pathDirRoot: Path) -> Optional[Dict]:
    """
    Derive folder path and album name from file's parent folder.
    
    Strategy: The immediate parent folder becomes the album name,
    any folders above that become the folder path.
    
    e.g., /NAS/photos/Vacation/Beach/2023/img.jpg
          -> folder_path: ['Vacation', 'Beach'], album: '2023'
    """
    pathRelative = pathFile.parent.relative_to(pathDirRoot)
    
    if pathRelative == Path('.'):
        return None  # File is in root, no album
    
    lStrParts = list(pathRelative.parts)
    
    if len(lStrParts) == 1:
        # Single folder - just an album at top level
        return {
            'folder_path': [],
            'album': lStrParts[0]
        }
    else:
        # Multiple levels - folders + album
        return {
            'folder_path': list(lStrParts[:-1]),
            'album': lStrParts[-1]
        }


def SetStrImportedFromResumeLog(pathResumeLog: Path) -> Set[str]:
    """
    Read nas_resume.txt and extract filenames that were successfully imported.
    """
    setStrImported = set()
    
    if not pathResumeLog.exists():
        return setStrImported
    
    with open(pathResumeLog, 'r') as fileResume:
        for strLine in fileResume:
            strLine = strLine.strip()
            if not strLine:
                continue
            
            lStrParts = strLine.split('\t')
            if len(lStrParts) < 3:
                continue
            
            strFilePath = lStrParts[1]
            strStatus = lStrParts[2]
            
            if strStatus == 'IMPORTED':
                setStrImported.add(strFilePath)
    
    return setStrImported


def PrepareActionPlan():
    """
    Scan source directory and create action plan YAML.
    """
    print(f"Source directory: {pathDirSource}")
    print(f"Staging directory: {pathDirStaging}")
    
    if not pathDirSource.is_dir():
        print(f"Error: {pathDirSource} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    # Create staging directory
    pathDirStaging.mkdir(parents=True, exist_ok=True)
    
    print("\nScanning for photos and videos...")
    lPathMedia, lPathUnhandled = LPathFileInDir(pathDirSource, pathDirStaging)
    
    cFileTotal = len(lPathMedia)
    cFileUnhandled = len(lPathUnhandled)
    print(f"Found {cFileTotal} media files")
    print(f"Found {cFileUnhandled} unhandled files")
    
    # Write unhandled files log
    if lPathUnhandled:
        pathUnhandled = pathDirStaging / 'unhandled.txt'
        print(f"\nWriting unhandled files list to {pathUnhandled}")
        with open(pathUnhandled, 'w') as fileUnhandled:
            for pathFile in sorted(lPathUnhandled):
                fileUnhandled.write(f"{pathFile}\n")
    
    if cFileTotal == 0:
        print("No media files found!")
        return
    
    # Build action plan
    objPlan = {
        'metadata': {
            'source_directory': str(pathDirSource),
            'total_files': cFileTotal,
            'unhandled_files': cFileUnhandled,
        },
        'folders': {},
        'albums': {},
        'actions': []
    }
    
    for pathFile in lPathMedia:
        objAlbumInfo = ObjAlbumInfoFromPath(pathFile, pathDirSource)
        
        objAction = {
            'source_file': str(pathFile),
            'filename': pathFile.name,
        }
        
        if objAlbumInfo:
            objAction['album_info'] = objAlbumInfo
            
            # Track folders
            lStrFolderPath = objAlbumInfo.get('folder_path', [])
            for i in range(len(lStrFolderPath)):
                strFolderKey = '/'.join(lStrFolderPath[:i+1])
                if strFolderKey not in objPlan['folders']:
                    objPlan['folders'][strFolderKey] = {'path': lStrFolderPath[:i+1]}
            
            # Track albums
            strAlbumKey = '/'.join(lStrFolderPath + [objAlbumInfo['album']])
            if strAlbumKey not in objPlan['albums']:
                objPlan['albums'][strAlbumKey] = {
                    'folder_path': lStrFolderPath,
                    'album': objAlbumInfo['album'],
                    'photo_count': 0
                }
            objPlan['albums'][strAlbumKey]['photo_count'] += 1
        
        objPlan['actions'].append(objAction)
    
    # Update metadata
    objPlan['metadata']['folder_count'] = len(objPlan['folders'])
    objPlan['metadata']['album_count'] = len(objPlan['albums'])
    
    # Write YAML
    pathYaml = pathDirStaging / 'nas_plan.yaml'
    print(f"\nWriting action plan to {pathYaml}")
    with open(pathYaml, 'w') as fileYaml:
        yaml.dump(objPlan, fileYaml, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    print(f"\n{'='*60}")
    print(f"Preparation complete!")
    print(f"{'='*60}")
    print(f"Media files:     {cFileTotal}")
    print(f"Unhandled files: {cFileUnhandled}")
    print(f"Folders:         {len(objPlan['folders'])}")
    print(f"Albums:          {len(objPlan['albums'])}")
    
    print(f"\nStructure preview:")
    for strAlbumKey in sorted(objPlan['albums'].keys()):
        objAlbum = objPlan['albums'][strAlbumKey]
        print(f"  {strAlbumKey}: {objAlbum['photo_count']} files")
    
    print(f"\nNext step: Run 'import' command:")
    print(f"  python nas.py import")


def FolderEnsure(libPhotos: photoscript.PhotosLibrary, lStrPath: List[str],
                 mpFolderCache: Dict[str, photoscript.Folder]) -> photoscript.Folder:
    """
    Ensure folder path exists, creating folders as needed.
    e.g., ['Vacation', 'Beach', '2023'] creates Vacation/Beach/2023
    
    Returns the innermost folder.
    """
    strCacheKey = '/'.join(lStrPath)
    
    if strCacheKey in mpFolderCache:
        return mpFolderCache[strCacheKey]
    
    folderCurrent = None
    strPathSoFar = ''
    
    for strFolderName in lStrPath:
        strPathSoFar = f"{strPathSoFar}/{strFolderName}" if strPathSoFar else strFolderName
        
        if strPathSoFar in mpFolderCache:
            folderCurrent = mpFolderCache[strPathSoFar]
            continue
        
        # Try to find existing folder
        if folderCurrent is None:
            # Top-level folder
            folderFound = libPhotos.folder(strFolderName)
        else:
            # Nested folder - search within parent
            folderFound = None
            for subfolder in folderCurrent.subfolders:
                if subfolder.name == strFolderName:
                    folderFound = subfolder
                    break
        
        if folderFound:
            folderCurrent = folderFound
        else:
            # Create folder
            if folderCurrent is None:
                folderCurrent = libPhotos.create_folder(strFolderName)
            else:
                folderCurrent = folderCurrent.create_folder(strFolderName)
        
        mpFolderCache[strPathSoFar] = folderCurrent
    
    return folderCurrent


def AlbumEnsureInFolder(libPhotos: photoscript.PhotosLibrary, 
                        lStrFolderPath: List[str], 
                        strAlbumName: str,
                        mpFolderCache: Dict[str, photoscript.Folder],
                        mpAlbumCache: Dict[str, photoscript.Album]) -> photoscript.Album:
    """
    Ensure album exists within folder path.
    e.g., (['Vacation', 'Beach'], '2023') creates album '2023' inside Vacation/Beach/
    """
    strCacheKey = '/'.join(lStrFolderPath + [strAlbumName])
    
    if strCacheKey in mpAlbumCache:
        return mpAlbumCache[strCacheKey]
    
    # Ensure folder path exists
    if lStrFolderPath:
        folder = FolderEnsure(libPhotos, lStrFolderPath, mpFolderCache)
        
        # Check if album exists in folder
        album = None
        for existingAlbum in folder.albums:
            if existingAlbum.name == strAlbumName:
                album = existingAlbum
                break
        
        if not album:
            album = folder.create_album(strAlbumName)
    else:
        # Top-level album
        album = libPhotos.album(strAlbumName)
        if not album:
            album = libPhotos.create_album(strAlbumName)
    
    mpAlbumCache[strCacheKey] = album
    return album


def ExecuteActionPlan():
    """Execute import action plan."""
    pathYaml = pathDirStaging / 'nas_plan.yaml'
    
    if not pathYaml.exists():
        print(f"Error: Action plan not found at {pathYaml}", file=sys.stderr)
        print(f"Run 'prep' command first.", file=sys.stderr)
        sys.exit(1)
    
    print(f"Loading action plan from {pathYaml}")
    
    with open(pathYaml, 'r') as fileYaml:
        objPlan = yaml.safe_load(fileYaml)
    
    lActions = objPlan['actions']
    cFileTotal = len(lActions)
    
    # Resume log
    pathResumeLog = pathDirStaging / 'nas_resume.txt'
    setStrImported = SetStrImportedFromResumeLog(pathResumeLog)
    
    print(f"\nAction Plan Summary:")
    print(f"  Total files:      {cFileTotal}")
    print(f"  Folders:          {len(objPlan.get('folders', {}))}")
    print(f"  Albums:           {len(objPlan.get('albums', {}))}")
    if setStrImported:
        print(f"  Already imported: {len(setStrImported)} files (will skip)")
    
    # Open Photos library
    print("\nOpening Photos library...")
    libPhotos = photoscript.PhotosLibrary()
    
    strLibraryNameCurrent = libPhotos.name
    if strLibraryNameCurrent.endswith('.photoslibrary'):
        strLibraryNameCurrent = strLibraryNameCurrent[:-14]
    
    if strLibraryNameCurrent != strLibraryNameExpected:
        print(f"\nError: Expected library '{strLibraryNameExpected}' but found '{strLibraryNameCurrent}'", file=sys.stderr)
        sys.exit(1)
    
    print(f"✓ Verified library: {strLibraryNameExpected}")
    
    mpFolderCache = {}
    mpAlbumCache = {}
    cFileImported = 0
    cFileSkipped = 0
    cFileAlreadyImported = 0
    cFileError = 0
    
    print(f"\n{'='*60}")
    print(f"Starting import...")
    print(f"{'='*60}\n")
    
    for iAction, objAction in enumerate(lActions):
        pathSource = Path(objAction['source_file'])
        strFilename = objAction['filename']
        strSourceFile = objAction['source_file']
        objAlbumInfo = objAction.get('album_info')
        
        # Skip if already imported (use full path for uniqueness)
        if strSourceFile in setStrImported:
            print(f"[{iAction + 1}/{cFileTotal}] ALREADY IMPORTED: {strFilename}")
            cFileAlreadyImported += 1
            continue
        
        if not pathSource.exists():
            print(f"[{iAction + 1}/{cFileTotal}] ERROR: File not found: {pathSource}")
            cFileError += 1
            continue
        
        try:
            print(f"[{iAction + 1}/{cFileTotal}] Importing {strFilename}")
            
            lPhotoImported = libPhotos.import_photos([str(pathSource)], skip_duplicate_check=False)
            
            if not lPhotoImported:
                print(f"  └─ SKIPPED (duplicate or failed)")
                cFileSkipped += 1
                with open(pathResumeLog, 'a') as fileResume:
                    fileResume.write(f"{iAction}\t{strSourceFile}\tSKIPPED\n")
                continue
            
            photoImported = lPhotoImported[0]
            cFileImported += 1
            
            # Add to album in folder structure
            if objAlbumInfo:
                lStrFolderPath = objAlbumInfo.get('folder_path', [])
                strAlbumName = objAlbumInfo.get('album')
                
                if lStrFolderPath:
                    strFullPath = '/'.join(lStrFolderPath) + '/' + strAlbumName
                else:
                    strFullPath = strAlbumName
                
                print(f"  └─ Adding to: {strFullPath}")
                albumTarget = AlbumEnsureInFolder(
                    libPhotos, lStrFolderPath, strAlbumName,
                    mpFolderCache, mpAlbumCache
                )
                albumTarget.add([photoImported])
            
            with open(pathResumeLog, 'a') as fileResume:
                fileResume.write(f"{iAction}\t{strSourceFile}\tIMPORTED\n")
            
        except Exception as err:
            print(f"  └─ ERROR: {err}")
            cFileError += 1
            with open(pathResumeLog, 'a') as fileResume:
                fileResume.write(f"{iAction}\t{strSourceFile}\tERROR\t{err}\n")
    
    print(f"\n{'='*60}")
    print(f"Import complete!")
    print(f"{'='*60}")
    print(f"Files imported:      {cFileImported}")
    print(f"Files skipped:       {cFileSkipped}")
    print(f"Already imported:    {cFileAlreadyImported}")
    print(f"Errors:              {cFileError}")
    print(f"\nSee '{pathResumeLog}' for detailed log.")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python nas.py prep    - Scan directory and create action plan")
        print("  python nas.py import  - Import files to Photos")
        print(f"\nConfiguration:")
        print(f"  Source:  {pathDirSource}")
        print(f"  Staging: {pathDirStaging}")
        print(f"  Library: {strLibraryNameExpected}")
        print(f"\nFiles:")
        print(f"  nas_plan.yaml  - Action plan")
        print(f"  nas_resume.txt - Import progress log")
        print(f"  unhandled.txt  - Non-media files found")
        sys.exit(1)
    
    strCommand = sys.argv[1]
    
    if strCommand == 'prep':
        PrepareActionPlan()
    elif strCommand == 'import':
        ExecuteActionPlan()
    else:
        print(f"Error: Unknown command '{strCommand}'")
        sys.exit(1)


if __name__ == '__main__':
    main()