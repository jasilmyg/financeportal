import sys
from dashboard_aggregator import get_dashboard_data
import os

res = get_dashboard_data('uploaded_sales_report.xlsx', '2026-07-10', 'daily', rbm='ALFAS')
print(res.get('category_performance', {}).get('qty', {}).get('CD PRODUCTS', {}))
