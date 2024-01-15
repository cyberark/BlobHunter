import csv
import os


def delete_csv():
    for file in os.listdir("."):
        if os.path.isfile(file) and file.startswith("public"):
            os.remove(file)


def write_csv(file_name, header, rows):
    file_exists = os.path.isfile(file_name)

    with open(file_name, 'a', newline='', encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)

        if not file_exists:
            writer.writerow(header)

        for r in rows:
            writer.writerow(r)
