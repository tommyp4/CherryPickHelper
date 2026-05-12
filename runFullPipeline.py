import subprocess
import sys

def run_command(cmd):
    print(f"\n>>> Running: {cmd}")
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

    # Step 0: Global Preferences
    compare_choice = input("❓ Run regression compare at the end? (y/n): ").lower().strip()
    checker_choice = input("❓ Run Author Name Checker? (y/n): ").lower().strip()

    # Check if Excel has blanks
    blanks_choice = 'n'
    # We ask upfront so we can pass it down
    blanks_choice = input("❓ Cherry-pick ALL blank entries? (y/n): ").lower().strip()

    pause_choice = input("❓ Pause at conflicts for manual resolution? (y/n): ").lower().strip()

    # Step 1: Optional Author Name Checker
    if checker_choice == 'y':
        if not run_command("py authorNameChecker.py"): return

    # Step 2: Gather Data
    if not run_command("py pullRequestHelper.py"): return

    # Step 3: Jira Enrichment
    if not run_command("py jiraHelper.py"): return

    # Step 4: Automated Cherry-Picking
    # Pass preferences as arguments to skip internal prompts
    cmd = f"py cherryPickHelper.py --blanks {blanks_choice} --pause {pause_choice}"
    if not run_command(cmd): return

    # Step 5: Final Verification
    if not run_command("py verifyBranchSync.py"): return

    # Step 6: Optional Regression Compare
    if compare_choice == 'y':
        if not run_command("py compare.py"): return

    print("\n" + "="*40)
    print("✅ FULL PIPELINE COMPLETED SUCCESSFULLY")
    print("="*40)

if __name__ == "__main__":
    main()
