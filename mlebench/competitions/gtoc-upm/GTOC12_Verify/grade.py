import subprocess
import logging
import os
from pathlib import Path

def grade(submission_path):
    print(f"=== DEBUG: grade function started ===")
    print(f"DEBUG: submission_path: {submission_path}")
    print(f"DEBUG: Current working directory: {os.getcwd()}")
    
    # Define paths
    verify_dir = '/mlebench/mlebench/competitions/gtoc-upm/GTOC12_Verify'
    verify_executable = os.path.join(verify_dir, 'GTOC12_Verify')
    result_file = os.path.join(verify_dir, 'Result.txt')
    
    print(f"DEBUG: verify_dir: {verify_dir}")
    print(f"DEBUG: verify_executable: {verify_executable}")
    print(f"DEBUG: result_file: {result_file}")
    
    # Check if verify directory exists
    verify_dir_path = Path(verify_dir)
    print(f"DEBUG: verify_dir exists: {verify_dir_path.exists()}")
    print(f"DEBUG: verify_dir is_dir: {verify_dir_path.is_dir()}")
    if verify_dir_path.exists():
        print(f"DEBUG: verify_dir permissions: {oct(verify_dir_path.stat().st_mode)}")
    
    # Check if GTOC12_Verify executable exists
    verify_exe_path = Path(verify_executable)
    print(f"DEBUG: verify_executable exists: {verify_exe_path.exists()}")
    print(f"DEBUG: verify_executable is_file: {verify_exe_path.is_file()}")
    
    if verify_exe_path.exists():
        print(f"DEBUG: verify_executable permissions: {oct(verify_exe_path.stat().st_mode)}")
        print(f"DEBUG: verify_executable is executable: {os.access(verify_executable, os.X_OK)}")
        
        # If not executable, make it executable
        if not os.access(verify_executable, os.X_OK):
            print(f"DEBUG: Making GTOC12_Verify executable...")
            try:
                os.chmod(verify_executable, 0o755)
                print(f"DEBUG: Successfully made GTOC12_Verify executable")
                print(f"DEBUG: New permissions: {oct(verify_exe_path.stat().st_mode)}")
            except Exception as e:
                print(f"DEBUG: Error making executable: {e}")
                raise e
    else:
        print(f"ERROR: GTOC12_Verify executable not found at {verify_executable}")
        # List directory contents to see what's there
        try:
            contents = list(verify_dir_path.iterdir()) if verify_dir_path.exists() else []
            print(f"DEBUG: Directory contents: {contents}")
        except Exception as e:
            print(f"DEBUG: Error listing directory: {e}")
        raise FileNotFoundError(f"GTOC12_Verify executable not found at {verify_executable}")
    
    # Check submission file
    submission_path_obj = Path(submission_path)
    print(f"DEBUG: submission file exists: {submission_path_obj.exists()}")
    print(f"DEBUG: submission file is_file: {submission_path_obj.is_file()}")
    if submission_path_obj.exists():
        print(f"DEBUG: submission file size: {submission_path_obj.stat().st_size} bytes")
    
    print(f"DEBUG: About to copy submission file...")
    try:
        # Copy the submission file to the current directory
        subprocess.run(['cp', submission_path, result_file], check=True)
        print(f"DEBUG: Successfully copied submission to {result_file}")
        
        # Verify the copy was successful
        result_file_path = Path(result_file)
        print(f"DEBUG: Result.txt exists after copy: {result_file_path.exists()}")
        if result_file_path.exists():
            print(f"DEBUG: Result.txt size: {result_file_path.stat().st_size} bytes")
    except subprocess.CalledProcessError as e:
        print(f"DEBUG: Error copying file: {e}")
        raise e
    
    # print the content of Result.txt for debugging
    try:
        with open(result_file, 'r') as f:
            content = f.read()
            print(f"DEBUG: Content of Result.txt:\n{content}")
    except Exception as e:
        print(f"DEBUG: Error reading Result.txt: {e}")
        raise e

    print(f"DEBUG: About to run GTOC12_Verify...")
    try:
        result = subprocess.run(
            ['./GTOC12_Verify'],
            cwd=verify_dir,
            capture_output=True,
            text=True,
            timeout=30  # Add timeout to prevent hanging
        )
        print(f"DEBUG: GTOC12_Verify execution completed")
        print(f"DEBUG: Return code: {result.returncode}")
        print(f"DEBUG: stdout: {result.stdout}")
        print(f"DEBUG: stderr: {result.stderr}")
        
        if result.returncode != 0:
            print(f"WARNING: GTOC12_Verify returned non-zero exit code: {result.returncode}")
            
    except subprocess.TimeoutExpired as e:
        print(f"DEBUG: GTOC12_Verify timed out: {e}")
        raise e
    except Exception as e:
        print(f"DEBUG: Error running GTOC12_Verify: {e}")
        raise e
    
    print(f"=== DEBUG: grade function completed ===")
    return str(result.stdout)
