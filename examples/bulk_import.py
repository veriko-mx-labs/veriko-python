"""Import beneficiaries in bulk: upload, preview, correct, commit.

export VERIKO_API_KEY=veriko_your_key_here
python examples/bulk_import.py beneficiaries.csv
"""

import sys

from veriko import Veriko

client = Veriko()

job = client.beneficiaries.import_start(sys.argv[1])
job = client.beneficiaries.import_wait(job.id)
print("Preview ready:", job.status)

for row in client.beneficiaries.iter_import_preview(job.id, buckets=["correctable", "fatal"]):
    print("Needs attention:", row.row_index, row.status, row.error_codes)

job = client.beneficiaries.import_commit(job.id)
print("Committed:", job.status)
