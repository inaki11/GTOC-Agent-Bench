import subprocess

def grade():
    print("Grading submission using GTOC12_Verify script. currently in /mlebench/competitions/gtoc-upm/grade.py")
    print("Current directory contents:")
    subprocess.run(['ls', '-l'], check=True)
    print("Executing GTOC12_Verify script...")
    result = subprocess.run(['./GTOC12_Verify'], capture_output=True, text=True)
    print("STDOUT:\n", result.stdout)
    return str(result.stdout)
