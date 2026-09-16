import os
import json
import traceback

def run_test():
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(workspace_dir, "data", "weekly_data_2026-06-02.json")
    pptx_template = r"c:\Users\rahul_myg\Downloads\Weekly TM review Template (1).pptx"
    
    excel_output = os.path.join(workspace_dir, "data", "test_output.xlsx")
    pptx_output = os.path.join(workspace_dir, "data", "test_output.pptx")
    
    print("Starting automated tests...")
    
    # 1. Read test data
    print(f"Reading test data from {data_path}...")
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print("Data loaded successfully.")
    except Exception as e:
        print(f"Error loading data: {e}")
        return False
        
    # 2. Import modules
    print("Importing excel_generator and pptx_updater...")
    try:
        from excel_generator import generate_excel_report
        from pptx_updater import update_pptx_slides
        print("Modules imported successfully.")
    except Exception as e:
        print(f"Error importing modules: {e}")
        traceback.print_exc()
        return False
        
    # 3. Test Excel Export
    print(f"Generating Excel report to {excel_output}...")
    try:
        generate_excel_report(data, excel_output)
        if os.path.exists(excel_output):
            print(f"Excel report generated successfully! Size: {os.path.getsize(excel_output)} bytes")
        else:
            print("Excel file was not created!")
            return False
    except Exception as e:
        print(f"Error during Excel generation: {e}")
        traceback.print_exc()
        return False
        
    # 4. Test PPTX Export
    print(f"Generating PPTX slide deck to {pptx_output}...")
    try:
        update_pptx_slides(pptx_template, data, pptx_output)
        if os.path.exists(pptx_output):
            print(f"PPTX deck generated successfully! Size: {os.path.getsize(pptx_output)} bytes")
        else:
            print("PPTX file was not created!")
            return False
    except Exception as e:
        print(f"Error during PPTX updating: {e}")
        traceback.print_exc()
        return False
        
    print("\nVerification Test Completed Successfully!")
    return True

if __name__ == "__main__":
    success = run_test()
    if not success:
        os._exit(1)
