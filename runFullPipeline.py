import subprocess
import sys

def run_command(cmd):
    print(f"\n>>> Running: {cmd}")
    # We use shell=True for Windows compatibility with 'py' command
    process = subprocess.Popen(cmd, shell=True)
    process.communicate()
    if process.returncode != 0:
        print(f"\n❌ Command failed with exit code {process.returncode}")
        return False
    return True

def main():
    print("========================================")
    print("   CHERRY-PICK HELPER FULL PIPELINE")
    print("========================================\n")

    # Step 2: Gather Data
    if not run_command("py pullRequestHelper.py"): return

    # Step 3: Jira Enrichment
    if not run_command("py jiraHelper.py"): return

    # Step 4: Automated Cherry-Picking
    # This step is interactive (asks about blanks/conflicts)
    if not run_command("py cherryPickHelper.py"): return

    # Step 5: Final Verification
    if not run_command("py verifyBranchSync.py"): return

    print("\n" + "="*40)
    print("✅ FULL PIPELINE COMPLETED SUCCESSFULLY")
    print("="*40)

if __name__ == "__main__":
    main()
