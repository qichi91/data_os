import csv
import json

def convert_understand_csv_to_digest(metrics_csv_path, output_json_path):
    functions_digest = []
    
    with open(metrics_csv_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 関数のメトリクスのみを抽出
            if row.get('Kind') in ['Function', 'C Member Function']:
                functions_digest.append({
                    "name": row.get('Name'),
                    "file": row.get('File'),
                    "sloc": int(row.get('CountLineCode', 0)),
                    "cyclomatic_complexity": int(row.get('Cyclomatic', 0)),
                    "fan_in": int(row.get('CountInput', 0)),
                    "fan_out": int(row.get('CountOutput', 0))
                })

    digest = {
        "project_summary": {
            "total_functions": len(functions_digest)
        },
        "functions": functions_digest
    }

    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    convert_understand_csv_to_digest('understand_metrics.csv', 'digest_input.json')
