`GET /health` checks that the API process is alive.
`POST /runs` uploads one document and runs the full pipeline synchronously for the POC.
`GET /runs/{run_id}` returns the stored Pydantic `PipelineRun` for one run.
`GET /runs` lists stored runs with customer, decision, and date filters.
`POST /query` answers grounded natural-language questions with tool evidence.
`GET /customers` lists configured YAML-backed customers.
