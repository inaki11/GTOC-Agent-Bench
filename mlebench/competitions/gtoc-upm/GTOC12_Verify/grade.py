import subprocess

def grade(submission_path):
    print("Grading submission using GTOC12_Verify script. currently in /mlebench/competitions/gtoc-upm/grade.py")
    print("Current directory contents:")
    print("Executing GTOC12_Verify script...")
    # copy the submission file to the current directory
    subprocess.run(['cp', submission_path, './Result_AGENT.txt'], check=True)
    subprocess.run(['ls', '-l'], check=True)
    result = subprocess.run(['./GTOC12_Verify'], capture_output=True, text=True)
    print("STDOUT:\n", result.stdout)
    return str(result.stdout)
