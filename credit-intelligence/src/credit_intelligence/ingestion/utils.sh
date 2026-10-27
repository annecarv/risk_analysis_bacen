cd ~/Documents/risk_analysis_bacen/credit-intelligence
ls -a

source .venv/bin/activate
pip install -r requirements.txt
python src/credit_intelligence/ingestion/bcb_taxas.py
