import subprocess
import logging

def grade(submission_path):
    # copy the submission file to the current directory
    subprocess.run(['cp', submission_path, '/mlebench/mlebench/competitions/gtoc-upm/GTOC12_Verify/Result.txt'], check=True)
    result = subprocess.run(
    ['./GTOC12_Verify'],
    cwd='/mlebench/mlebench/competitions/gtoc-upm/GTOC12_Verify',
    capture_output=True,
    text=True
)

    return str(result.stdout)
