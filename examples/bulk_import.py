"""Importa beneficiarios en lote: sube el archivo, revisa la vista previa, corrige y confirma.

export VERIKO_API_KEY=veriko_tu_clave_aqui
python examples/bulk_import.py beneficiarios.csv
"""

import sys

from veriko import Veriko

client = Veriko()

job = client.beneficiaries.import_start(sys.argv[1])
job = client.beneficiaries.import_wait(job.id)
print("Vista previa lista:", job.status)

for row in client.beneficiaries.iter_import_preview(job.id, buckets=["correctable", "fatal"]):
    print("Requiere atención:", row.row_index, row.status, row.error_codes)

job = client.beneficiaries.import_commit(job.id)
print("Confirmada:", job.status)
