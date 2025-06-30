import os
from pathlib import Path

from flask import Flask, jsonify, request

from mlebench.grade import validate_submission
from mlebench.registry import registry

app = Flask(__name__)

PRIVATE_DATA_DIR = "/private/data"
COMPETITION_ID = os.getenv("COMPETITION_ID")  # This is populated for us at container runtime


def run_validation(submission: Path) -> str:
    print(f"=== DEBUG: run_validation started ===")
    print(f"Validating submission: (environment/grading_server.py)")
    print(f"Submission path: {submission}")
    print(f"Submission exists: {submission.exists()}")
    print(f"Submission is file: {submission.is_file()}")
    if submission.exists():
        print(f"Submission size: {submission.stat().st_size} bytes")
        print(f"Submission permissions: {oct(submission.stat().st_mode)[-3:]}")
    
    print(f"PRIVATE_DATA_DIR: {PRIVATE_DATA_DIR}")
    print(f"PRIVATE_DATA_DIR exists: {Path(PRIVATE_DATA_DIR).exists()}")
    print(f"COMPETITION_ID: {COMPETITION_ID}")
    
    print(f"Creating new registry with data dir: {PRIVATE_DATA_DIR}")
    new_registry = registry.set_data_dir(Path(PRIVATE_DATA_DIR))
    print(f"Registry created successfully")
    
    print(f"Getting competition: {COMPETITION_ID}")
    competition = new_registry.get_competition(COMPETITION_ID)
    print(f"Competition ID: {competition.id}")
    print(f"Competition grader: {competition.grader}")
    
    print(f"Calling validate_submission...")
    try:
        is_valid, message = validate_submission(submission, competition)
        print(f"Validation result - is_valid: {is_valid}")
        print(f"Validation result - message: {message}")
    except Exception as e:
        print(f"ERROR in validate_submission: {e}")
        print(f"Error type: {type(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        raise
    
    print(f"=== DEBUG: run_validation completed ===")
    return message


@app.route("/validate", methods=["POST"])
def validate():
    print(f"=== DEBUG: /validate endpoint called ===")
    print(f"Request method: {request.method}")
    print(f"Request files: {request.files}")
    print(f"Request form: {request.form}")
    
    if "file" not in request.files:
        print(f"ERROR: No file in request")
        return jsonify({"error": "No file provided"}), 400
    
    submission_file = request.files["file"]
    print(f"Submission file: {submission_file}")
    print(f"Submission filename: {submission_file.filename}")
    print(f"Submission content type: {submission_file.content_type}")
    
    submission_path = Path("/tmp/submission_to_validate.txt")
    print(f"Saving to: {submission_path}")
    
    try:
        submission_file.save(submission_path)
        print(f"File saved successfully")
        print(f"Saved file exists: {submission_path.exists()}")
        if submission_path.exists():
            print(f"Saved file size: {submission_path.stat().st_size} bytes")
    except Exception as e:
        print(f"ERROR saving file: {e}")
        return jsonify({"error": "Failed to save file", "details": str(e)}), 500

    try:
        print(f"Calling run_validation...")
        result = run_validation(submission_path)
        print(f"Validation completed with result: {result}")
    except Exception as e:
        print(f"ERROR in run_validation: {e}")
        print(f"Error type: {type(e)}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        # Server error
        return jsonify({"error": "An unexpected error occurred.", "details": str(e)}), 500

    print(f"=== DEBUG: /validate endpoint completed successfully ===")
    return jsonify({"result": result})


@app.route("/health", methods=["GET"])
def health():
    print(f"Health check called")
    return jsonify({"status": "running"}), 200


if __name__ == "__main__":
    print(f"=== DEBUG: Starting Flask app ===")
    print(f"PRIVATE_DATA_DIR: {PRIVATE_DATA_DIR}")
    print(f"COMPETITION_ID: {COMPETITION_ID}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Environment variables: {dict(os.environ)}")
    app.run(host="0.0.0.0", port=5000)
