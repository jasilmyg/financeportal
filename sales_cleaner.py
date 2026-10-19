import os
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────# ─────────────────────────────────────────────────────────────────────────────
# Store → Fin BDM / RBM / BDM / Zone mapping
# Source: DATA.xlsx (authoritative cleaned reference)
# ─────────────────────────────────────────────────────────────────────────────
STORE_FIN_BDM_MAP = {
    'AAKKULAM FUTURE':       'Ashik',
    'ADIMALI':               'Febin',
    'ADOOR':                 'Jackson',
    'ADOOR FUTURE':          'Jackson',
    'ALAPPUZHA':             'Jackson',
    'ALAPPUZHA FUTURE':      'Jackson',
    'ALUVA':                 'Arun',
    'ALUVA FUTURE':          'Arun',
    'ANGAMALY':              'Febin',
    'ANGAMALY FUTURE':       'Febin',
    'AREEKODE FUTURE':       'Siraj',
    'ATTINGAL':              'Ashik',
    'ATTINGAL FUTURE':       'Ashik',
    'BALUSSERY':             'Nikhil',
    'BALUSSERY FUTURE':      'Nikhil',
    'BANK ROAD':             'Sreeju',
    'BATHERY FUTURE':        'Nikhil',
    'BYPASS ROAD':           'Siraj',
    'CAMWORLD':              'Aswin',
    'CHALAKKUDI':            'Febin',
    'CHALAKKUDY FUTURE':     'Febin',
    'CHANGANASSERY':         'Jackson',
    'CHAVAKKAD FUTURE':      'Febin',
    'CHEMMAD FUTURE':        'Siraj',
    'CHERTHALA':             'Jackson',
    'CHERTHALA FUTURE':      'Jackson',
    'EAST FORT':             'Febin',
    'EDAKKARA':              'Siraj',
    'EDAPPAL':               'Siraj',
    'EDAPPAL FUTURE':        'Siraj',
    'EDAPPALLY FUTURE':      'Arun',
    'ERATTUPETTA FUTURE':    'Jackson',
    'FEROKE FUTURE':         'Aswin',
    'FORUM MALL':            'Arun',
    'HARIPAD FUTURE':        'Jackson',
    'HI LITE MALL':          'Aswin',
    'IRINJALAKUDA FUTURE':   'Febin',
    'IRITTY':                'Sreeju',
    'KADAVANTHRA':           'Arun',
    'KALPETTA':              'Nikhil',
    'KALPETTA FUTURE':       'Nikhil',
    'KANHANGAD':             'Sreeju',
    'KANHANGAD FUTURE':      'Sreeju',
    'KANJIRAPPALLY FUTURE':  'Jackson',
    'KANNUR FUTURE':         'Sreeju',
    'KARAMANA':              'Ashik',
    'KARUNAGAPPALLY':        'Ashik',
    'KARUNAGAPPALLY FUTURE': 'Ashik',
    'KASARGOD':              'Sreeju',
    'KASARGOD FUTURE':       'Sreeju',
    'KATTAPPANA FUTURE':     'Febin',
    'KAYAMKULAM':            'Jackson',
    'KILIMANOOR':            'Ashik',
    'KINGSWAY':              'Aswin',
    'KOLLAM':                'Ashik',
    'KOLLAM FUTURE':         'Ashik',
    'KONDOTTY':              'Siraj',
    'KONDOTTY FUTURE':       'Siraj',
    'KOTHAMANGALAM FUTURE':  'Febin',
    'KOTTAKKAL':             'Siraj',
    'KOTTAKKAL FUTURE':      'Siraj',
    'KOTTARAKKARA':          'Ashik',
    'KOTTAYAM FUTURE':       'Jackson',
    'KOYILANDY':             'Aswin',
    'KOYILANDY 2':           'Aswin',
    'KUNNAMANGALAM FUTURE':  'Nikhil',
    'KUNNAMKULAM':           'Febin',
    'KURUPPAM ROAD':         'Febin',
    'KUTTIYADI':             'Aswin',
    'KUTTIYADI FUTURE':      'Aswin',
    'LANDSHIP MALL':         'Aswin',
    'MAHE':                  'Sreeju',
    'MALAPPURAM':            'Siraj',
    'MANANTHAVADY':          'Nikhil',
    'MANANTHAVADY FUTURE':   'Nikhil',
    'MANJERY':               'Siraj',
    'MANNARKAD FUTURE':      'Siraj',
    'MARINE DRIVE FUTURE':   'Arun',
    'MAVELIKARA FUTURE':     'Jackson',
    'MENAKA':                'Arun',
    'MUKKAM FUTURE':         'Nikhil',
    'MUKKOM':                'Nikhil',
    'MUVATTUPUZHA':          'Arun',
    'MUVATTUPUZHA FUTURE':   'Arun',
    'MYG CARE BATHERY':      'Nikhil',
    'MYG CARE PERUMANNA':    'Nikhil',
    'NADAKKAVU FUTURE':      'Aswin',
    'NAGAMPADAM':            'Jackson',
    'NARIKKUNI':             'Nikhil',
    'NILAMBUR FUTURE':       'Siraj',
    'OTTAPALAM':             'Siraj',
    'OTTUPARA FUTURE':       'Febin',
    'P TOWER':               'Aswin',
    'PALAKKAD FUTURE':       'Siraj',
    'PALARIVATTOM':          'Arun',
    'PANAVILA FUTURE':       'Ashik',
    'PARAVOOR':              'Arun',
    'PATHANAMTHITTA FUTURE': 'Jackson',
    'PATTAMBI FUTURE':       'Siraj',
    'PATTOM':                'Ashik',
    'PAYYANNUR':             'Sreeju',
    'PAYYANNUR FUTURE':      'Sreeju',
    'PAZHAVANGADI':          'Ashik',
    'PERAMBRA':              'Aswin',
    'PERAMBRA FUTURE':       'Aswin',
    'PERINTHALMANNA FUTURE': 'Siraj',
    'PERUMBAVOOR FUTURE':    'Febin',
    'PIRAVOM FUTURE':        'Arun',
    'POOTHOLE FUTURE':       'Febin',
    'POTTAMMAL FUTURE':      'Aswin',
    'PUNALUR FUTURE':        'Ashik',
    'PUTHIYARA':             'Aswin',
    'RAMANATTUKARA':         'Nikhil',
    'RANNI FUTURE':          'Jackson',
    'SABA':                  'Aswin',
    'SABA MAHARANI':         'Aswin',
    'SNPARK':                'Sreeju',
    'TALIPARAMBA':           'Sreeju',
    'TALIPARAMBA FUTURE':    'Sreeju',
    'TB ROAD':               'Siraj',
    'THALASSERY':            'Sreeju',
    'THALASSERY FUTURE':     'Sreeju',
    'THAMARASSERY FUTURE':   'Nikhil',
    'THANA':                 'Sreeju',
    'THIRUNAKKARA':          'Jackson',
    'THIRUVALLA':            'Jackson',
    'THIRUVALLA FUTURE':     'Jackson',
    'THODUPUZHA':            'Febin',
    'THODUPUZHA FUTURE':     'Febin',
    'THONDAYAD FUTURE':      'Nikhil',
    'THOPPUMPADY FUTURE':    'Arun',
    'THRIPUNITHURA':         'Arun',
    'THRIPUNITHURA FUTURE':  'Arun',
    'TIRUR':                 'Siraj',
    'UPPALA':                'Sreeju',
    'VADAKARA':              'Aswin',
    'VADAKARA FUTURE':       'Aswin',
    'VADAKKENCHERRY FUTURE': 'Siraj',
    'VALANCHERY FUTURE':     'Siraj',
    'VENGARA':               'Siraj',
    'VENJARAMOODU FUTURE':   'Ashik',
    'VYPIN FUTURE':          'Arun',
    'WANDOOR':               'Siraj',
}

# Store → RBM mapping (from DATA.xlsx)
STORE_RBM_MAP = {
    'AAKKULAM FUTURE':       'Vaisakh',
    'ADIMALI':               'Renjith',
    'ADOOR':                 'Mahesh',
    'ADOOR FUTURE':          'Mahesh',
    'ALAPPUZHA':             'Mahesh',
    'ALAPPUZHA FUTURE':      'Mahesh',
    'ALUVA':                 'Renjith',
    'ALUVA FUTURE':          'Renjith',
    'ANGAMALY':              'Renjith',
    'ANGAMALY FUTURE':       'Renjith',
    'AREEKODE FUTURE':       'Sameer',
    'ATTINGAL':              'Vaisakh',
    'ATTINGAL FUTURE':       'Vaisakh',
    'BALUSSERY':             'Alfas',
    'BALUSSERY FUTURE':      'Alfas',
    'BANK ROAD':             'Sameem',
    'BATHERY FUTURE':        'Alfas',
    'BYPASS ROAD':           'Sameer',
    'CAMWORLD':              'Alfas',
    'CHALAKKUDI':            'Renjith',
    'CHALAKKUDY FUTURE':     'Renjith',
    'CHANGANASSERY':         'Mahesh',
    'CHAVAKKAD FUTURE':      'Renjith',
    'CHEMMAD FUTURE':        'Sameer',
    'CHERTHALA':             'Mahesh',
    'CHERTHALA FUTURE':      'Mahesh',
    'EAST FORT':             'Renjith',
    'EDAKKARA':              'Sameer',
    'EDAPPAL':               'Sameer',
    'EDAPPAL FUTURE':        'Sameer',
    'EDAPPALLY FUTURE':      'Renjith',
    'ERATTUPETTA FUTURE':    'Mahesh',
    'FEROKE FUTURE':         'Alfas',
    'FORUM MALL':            'Renjith',
    'HARIPAD FUTURE':        'Mahesh',
    'HI LITE MALL':          'Alfas',
    'IRINJALAKUDA FUTURE':   'Renjith',
    'IRITTY':                'Sameem',
    'KADAVANTHRA':           'Renjith',
    'KALPETTA':              'Alfas',
    'KALPETTA FUTURE':       'Alfas',
    'KANHANGAD':             'Sameem',
    'KANHANGAD FUTURE':      'Sameem',
    'KANJIRAPPALLY FUTURE':  'Mahesh',
    'KANNUR FUTURE':         'Sameem',
    'KARAMANA':              'Vaisakh',
    'KARUNAGAPPALLY':        'Vaisakh',
    'KARUNAGAPPALLY FUTURE': 'Vaisakh',
    'KASARGOD':              'Sameem',
    'KASARGOD FUTURE':       'Sameem',
    'KATTAPPANA FUTURE':     'Renjith',
    'KAYAMKULAM':            'Mahesh',
    'KILIMANOOR':            'Vaisakh',
    'KINGSWAY':              'Alfas',
    'KOLLAM':                'Vaisakh',
    'KOLLAM FUTURE':         'Vaisakh',
    'KONDOTTY':              'Sameer',
    'KONDOTTY FUTURE':       'Sameer',
    'KOTHAMANGALAM FUTURE':  'Renjith',
    'KOTTAKKAL':             'Sameer',
    'KOTTAKKAL FUTURE':      'Sameer',
    'KOTTARAKKARA':          'Vaisakh',
    'KOTTAYAM FUTURE':       'Mahesh',
    'KOYILANDY':             'Alfas',
    'KOYILANDY 2':           'Alfas',
    'KUNNAMANGALAM FUTURE':  'Alfas',
    'KUNNAMKULAM':           'Renjith',
    'KURUPPAM ROAD':         'Renjith',
    'KUTTIYADI':             'Alfas',
    'KUTTIYADI FUTURE':      'Alfas',
    'LANDSHIP MALL':         'Alfas',
    'MAHE':                  'Sameem',
    'MALAPPURAM':            'Sameer',
    'MANANTHAVADY':          'Alfas',
    'MANANTHAVADY FUTURE':   'Alfas',
    'MANJERY':               'Sameer',
    'MANNARKAD FUTURE':      'Sameer',
    'MARINE DRIVE FUTURE':   'Renjith',
    'MAVELIKARA FUTURE':     'Mahesh',
    'MENAKA':                'Renjith',
    'MUKKAM FUTURE':         'Alfas',
    'MUKKOM':                'Alfas',
    'MUVATTUPUZHA':          'Renjith',
    'MUVATTUPUZHA FUTURE':   'Renjith',
    'MYG CARE BATHERY':      'Alfas',
    'MYG CARE PERUMANNA':    'Alfas',
    'NADAKKAVU FUTURE':      'Alfas',
    'NAGAMPADAM':            'Mahesh',
    'NARIKKUNI':             'Alfas',
    'NILAMBUR FUTURE':       'Sameer',
    'OTTAPALAM':             'Sameer',
    'OTTUPARA FUTURE':       'Renjith',
    'P TOWER':               'Alfas',
    'PALAKKAD FUTURE':       'Sameer',
    'PALARIVATTOM':          'Renjith',
    'PANAVILA FUTURE':       'Vaisakh',
    'PARAVOOR':              'Renjith',
    'PATHANAMTHITTA FUTURE': 'Mahesh',
    'PATTAMBI FUTURE':       'Sameer',
    'PATTOM':                'Vaisakh',
    'PAYYANNUR':             'Sameem',
    'PAYYANNUR FUTURE':      'Sameem',
    'PAZHAVANGADI':          'Vaisakh',
    'PERAMBRA':              'Alfas',
    'PERAMBRA FUTURE':       'Alfas',
    'PERINTHALMANNA FUTURE': 'Sameer',
    'PERUMBAVOOR FUTURE':    'Renjith',
    'PIRAVOM FUTURE':        'Renjith',
    'POOTHOLE FUTURE':       'Renjith',
    'POTTAMMAL FUTURE':      'Alfas',
    'PUNALUR FUTURE':        'Vaisakh',
    'PUTHIYARA':             'Alfas',
    'RAMANATTUKARA':         'Alfas',
    'RANNI FUTURE':          'Mahesh',
    'SABA':                  'Alfas',
    'SABA MAHARANI':         'Alfas',
    'SNPARK':                'Sameem',
    'TALIPARAMBA':           'Sameem',
    'TALIPARAMBA FUTURE':    'Sameem',
    'TB ROAD':               'Sameer',
    'THALASSERY':            'Sameem',
    'THALASSERY FUTURE':     'Sameem',
    'THAMARASSERY FUTURE':   'Alfas',
    'THANA':                 'Sameem',
    'THIRUNAKKARA':          'Mahesh',
    'THIRUVALLA':            'Mahesh',
    'THIRUVALLA FUTURE':     'Mahesh',
    'THODUPUZHA':            'Renjith',
    'THODUPUZHA FUTURE':     'Renjith',
    'THONDAYAD FUTURE':      'Alfas',
    'THOPPUMPADY FUTURE':    'Renjith',
    'THRIPUNITHURA':         'Renjith',
    'THRIPUNITHURA FUTURE':  'Renjith',
    'TIRUR':                 'Sameer',
    'UPPALA':                'Sameem',
    'VADAKARA':              'Alfas',
    'VADAKARA FUTURE':       'Alfas',
    'VADAKKENCHERRY FUTURE': 'Sameer',
    'VALANCHERY FUTURE':     'Sameer',
    'VENGARA':               'Sameer',
    'VENJARAMOODU FUTURE':   'Vaisakh',
    'VYPIN FUTURE':          'Renjith',
    'WANDOOR':               'Sameer',
}

# Store → BDM mapping (from DATA.xlsx)
STORE_BDM_MAP = {
    'AAKKULAM FUTURE':       'Amal',
    'ADIMALI':               'Sreerag',
    'ADOOR':                 'Siju',
    'ADOOR FUTURE':          'Siju',
    'ALAPPUZHA':             'Renjithmon',
    'ALAPPUZHA FUTURE':      'Renjithmon',
    'ALUVA':                 'Salaj',
    'ALUVA FUTURE':          'Salaj',
    'ANGAMALY':              'Jaleel',
    'ANGAMALY FUTURE':       'Jaleel',
    'AREEKODE FUTURE':       'Pranav',
    'ATTINGAL':              'Abhiraj',
    'ATTINGAL FUTURE':       'Abhiraj',
    'BALUSSERY':             'Nideesh',
    'BALUSSERY FUTURE':      'Nideesh',
    'BANK ROAD':             'Faisal',
    'BATHERY FUTURE':        'Jobin',
    'BYPASS ROAD':           'Ashif',
    'CAMWORLD':              'Sarath',
    'CHALAKKUDI':            'Gopakumar',
    'CHALAKKUDY FUTURE':     'Gopakumar',
    'CHANGANASSERY':         'Shameer',
    'CHAVAKKAD FUTURE':      'Gopakumar',
    'CHEMMAD FUTURE':        'Pranav',
    'CHERTHALA':             'Renjithmon',
    'CHERTHALA FUTURE':      'Renjithmon',
    'EAST FORT':             'Praseed',
    'EDAKKARA':              'Pranav',
    'EDAPPAL':               'Manu',
    'EDAPPAL FUTURE':        'Manu',
    'EDAPPALLY FUTURE':      'Salaj',
    'ERATTUPETTA FUTURE':    'Shameer',
    'FEROKE FUTURE':         'Sarath',
    'FORUM MALL':            'Mobish',
    'HARIPAD FUTURE':        'Renjithmon',
    'HI LITE MALL':          'Sarath',
    'IRINJALAKUDA FUTURE':   'Gopakumar',
    'IRITTY':                'Abhilash',
    'KADAVANTHRA':           'Salaj',
    'KALPETTA':              'Jobin',
    'KALPETTA FUTURE':       'Jobin',
    'KANHANGAD':             'Anoop',
    'KANHANGAD FUTURE':      'Anoop',
    'KANJIRAPPALLY FUTURE':  'Shameer',
    'KANNUR FUTURE':         'Abhilash',
    'KARAMANA':              'Amal',
    'KARUNAGAPPALLY':        'Niyad',
    'KARUNAGAPPALLY FUTURE': 'Niyad',
    'KASARGOD':              'Anoop',
    'KASARGOD FUTURE':       'Anoop',
    'KATTAPPANA FUTURE':     'Sreerag',
    'KAYAMKULAM':            'Renjithmon',
    'KILIMANOOR':            'Abhiraj',
    'KINGSWAY':              'Sarath',
    'KOLLAM':                'Niyad',
    'KOLLAM FUTURE':         'Niyad',
    'KONDOTTY':              'Pranav',
    'KONDOTTY FUTURE':       'Pranav',
    'KOTHAMANGALAM FUTURE':  'Jaleel',
    'KOTTAKKAL':             'Manu',
    'KOTTAKKAL FUTURE':      'Manu',
    'KOTTARAKKARA':          'Niyad',
    'KOTTAYAM FUTURE':       'Shameer',
    'KOYILANDY':             'Shafeek',
    'KOYILANDY 2':           'Shafeek',
    'KUNNAMANGALAM FUTURE':  'Nideesh',
    'KUNNAMKULAM':           'Praseed',
    'KURUPPAM ROAD':         'Praseed',
    'KUTTIYADI':             'Shafeek',
    'KUTTIYADI FUTURE':      'Shafeek',
    'LANDSHIP MALL':         'Sarath',
    'MAHE':                  'Abhilash',
    'MALAPPURAM':            'Manu',
    'MANANTHAVADY':          'Jobin',
    'MANANTHAVADY FUTURE':   'Jobin',
    'MANJERY':               'Pranav',
    'MANNARKAD FUTURE':      'Ashif',
    'MARINE DRIVE FUTURE':   'Mobish',
    'MAVELIKARA FUTURE':     'Renjithmon',
    'MENAKA':                'Mobish',
    'MUKKAM FUTURE':         'Nideesh',
    'MUKKOM':                'Nideesh',
    'MUVATTUPUZHA':          'Jaleel',
    'MUVATTUPUZHA FUTURE':   'Jaleel',
    'MYG CARE BATHERY':      'Jobin',
    'MYG CARE PERUMANNA':    'Nideesh',
    'NADAKKAVU FUTURE':      'Sarath',
    'NAGAMPADAM':            'Shameer',
    'NARIKKUNI':             'Nideesh',
    'NILAMBUR FUTURE':       'Pranav',
    'OTTAPALAM':             'Ashif',
    'OTTUPARA FUTURE':       'Praseed',
    'P TOWER':               'Sarath',
    'PALAKKAD FUTURE':       'Ashif',
    'PALARIVATTOM':          'Salaj',
    'PANAVILA FUTURE':       'Amal',
    'PARAVOOR':              'Salaj',
    'PATHANAMTHITTA FUTURE': 'Siju',
    'PATTAMBI FUTURE':       'Ashif',
    'PATTOM':                'Amal',
    'PAYYANNUR':             'Faisal',
    'PAYYANNUR FUTURE':      'Faisal',
    'PAZHAVANGADI':          'Amal',
    'PERAMBRA':              'Shafeek',
    'PERAMBRA FUTURE':       'Shafeek',
    'PERINTHALMANNA FUTURE': 'Manu',
    'PERUMBAVOOR FUTURE':    'Jaleel',
    'PIRAVOM FUTURE':        'Jaleel',
    'POOTHOLE FUTURE':       'Praseed',
    'POTTAMMAL FUTURE':      'Sarath',
    'PUNALUR FUTURE':        'Niyad',
    'PUTHIYARA':             'Sarath',
    'RAMANATTUKARA':         'Nideesh',
    'RANNI FUTURE':          'Siju',
    'SABA':                  'Sarath',
    'SABA MAHARANI':         'Sarath',
    'SNPARK':                'Faisal',
    'TALIPARAMBA':           'Faisal',
    'TALIPARAMBA FUTURE':    'Faisal',
    'TB ROAD':               'Ashif',
    'THALASSERY':            'Abhilash',
    'THALASSERY FUTURE':     'Abhilash',
    'THAMARASSERY FUTURE':   'Nideesh',
    'THANA':                 'Abhilash',
    'THIRUNAKKARA':          'Shameer',
    'THIRUVALLA':            'Siju',
    'THIRUVALLA FUTURE':     'Siju',
    'THODUPUZHA':            'Sreerag',
    'THODUPUZHA FUTURE':     'Sreerag',
    'THONDAYAD FUTURE':      'Nideesh',
    'THOPPUMPADY FUTURE':    'Mobish',
    'THRIPUNITHURA':         'Mobish',
    'THRIPUNITHURA FUTURE':  'Mobish',
    'TIRUR':                 'Manu',
    'UPPALA':                'Anoop',
    'VADAKARA':              'Shafeek',
    'VADAKARA FUTURE':       'Shafeek',
    'VADAKKENCHERRY FUTURE': 'Ashif',
    'VALANCHERY FUTURE':     'Manu',
    'VENGARA':               'Pranav',
    'VENJARAMOODU FUTURE':   'Abhiraj',
    'VYPIN FUTURE':          'Salaj',
    'WANDOOR':               'Pranav',
}

# Store → Zone mapping (from DATA.xlsx)
STORE_ZONE_MAP = {
    store: 'SOUTH' if rbm in ('Renjith','Mahesh','Vaisakh') else 'NORTH'
    for store, rbm in STORE_RBM_MAP.items()
}

# ─────────────────────────────────────────────────────────────────────────────
# Product → Category mapping  (casing matches reference exactly)
# ─────────────────────────────────────────────────────────────────────────────
PRODUCT_CATEGORY_MAP = {
    # CD Products (casing from DATA.xlsx)
    'AIR CONDITIONER':  'CD Products',
    'DISH WASHER':      'CD Products',
    'DRYER':            'CD Products',
    'FREEZER':          'CD Products',
    'HOME APPLIANCES':  'CD Products',
    'MICROWAVE OVEN':   'CD Products',
    'REFRIGERATORS':    'CD Products',
    'TV':               'CD Products',
    'WASHING MACHINES': 'CD Products',
    # HVA
    'AUDIO':        'HVA',
    'CEGI':         'HVA',
    'EAR WEARABLES':'HVA',
    'GAMING':       'HVA',
    'HOME THEATRE': 'HVA',
    'SMART WATCH':  'HVA',
    # Laptop
    'LAPTOP':   'LAPTOP',
    'DESKTOP':  'LAPTOP',
    # Mobile
    'MOBILE':  'MOBILE',
    'TABLET':  'MOBILE',
    'TAB':     'MOBILE',
    # SA  (any product containing 'SMALL APPLIANCE' handled separately below)
    'SMALL APPLIANCES': 'SA',
    # Others
    'CAMERA':        'Others',
    'DIY':           'Others',
    'IT ACCESSORIES':'Others',
    'PRINTER':       'Others',
    'RIG':           'Others',
    'STABILIZER':    'Others',
}

# ─────────────────────────────────────────────────────────────────────────────
# Blacklisted products → removed from output
# ─────────────────────────────────────────────────────────────────────────────
BLACKLIST_PRODUCTS = {
    # From user spec
    'ACCESSORIES',
    'CARE PLUS',
    'CROCKERY',
    'D SPARE',
    'GLAM SHIELD',
    'GLAMSHIELD',
    'HOUSE HOLD',
    'HOUSEHOLD',
    'LG AMC',
    'OSG WARRANTY',
    'PERSONAL CARE',
    'PROTECT MAX',
    'SERVICE',
    'SMART CHOICE',
    'STORAGE DEVICES',
    # Additional — found by comparing with DATA.xlsx reference
    'GDOT CARE',
    'DEMO',
    'DEMO ACCESSORIES',
    'DEMO LAPTOP',
    'SCHEME',
    'MYG VERSE',
    'MYG DOMO',
    'GIFT ITEMS',
    'LAPTOP BAG',
    'FOC',
    'SERVICE CHARGES',
    'FRAGRANCE',
    'IT PRODUCT',
    'MONITOR',
    'CCTV',
    'SPARE',
    'AC OUTDOOR',
}


def _normalize_columns(df):
    """
    Strip whitespace from all column names and attempt case-insensitive
    matching to map common variations to the exact names expected by
    clean_sales_report().  Returns the renamed DataFrame.
    """
    # Map of canonical name → list of acceptable aliases (all UPPER, stripped)
    COLUMN_ALIASES = {
        'Slno':               ['SLNO', 'SL NO', 'SL.NO', 'S.NO', 'SNO', 'SR NO', 'SR.NO', 'SERIAL NO', 'SERIAL NUMBER'],
        'Branch':             ['BRANCH', 'STORE', 'BRANCH NAME', 'STORE NAME', 'OUTLET', 'SHOP NAME'],
        'RBM':                ['RBM', 'REGIONAL BUSINESS MANAGER', 'REGIONAL BDM', 'REGION MANAGER'],
        'BDM':                ['BDM', 'BUSINESS DEVELOPMENT MANAGER', 'BD MANAGER'],
        'Date':               ['DATE', 'SALE DATE', 'INVOICE DATE', 'TRANSACTION DATE', 'BILL DATE'],
        'Time':               ['TIME', 'MONTH', 'PERIOD', 'WEEK'],
        'Invoice Number':     ['INVOICE NUMBER', 'INVOICE NO', 'INVOICE', 'BILL NUMBER', 'BILL NO'],
        'Customer Name':      ['CUSTOMER NAME', 'CUST NAME', 'CLIENT NAME', 'BUYER NAME', 'CUSTOMER'],
        'Customer Mobile':    ['CUSTOMER MOBILE', 'MOBILE', 'MOBILE NUMBER', 'PHONE', 'PHONE NUMBER', 'CONTACT NUMBER', 'CUST MOBILE'],
        'Pincode':            ['PINCODE', 'PIN CODE', 'ZIP', 'POSTAL CODE'],
        'Staff':              ['STAFF', 'STAFF NAME', 'SALESPERSON', 'SALES PERSON', 'EMPLOYEE', 'SALES STAFF'],
        'Product':            ['PRODUCT', 'PRODUCT NAME', 'PRODUCT TYPE', 'ITEM TYPE', 'CATEGORY NAME'],
        'Brand':              ['BRAND', 'BRAND NAME', 'MAKE', 'MANUFACTURER'],
        'Item Group':         ['ITEM GROUP', 'ITEM GRP', 'GROUP', 'PRODUCT GROUP'],
        'Item Category':      ['ITEM CATEGORY', 'ITEM CAT', 'SUB CATEGORY', 'SUBCATEGORY'],
        'Item Code':          ['ITEM CODE', 'PRODUCT CODE', 'SKU', 'MODEL CODE', 'CODE'],
        'Item Name':          ['ITEM NAME', 'PRODUCT DESCRIPTION', 'MODEL NAME', 'DESCRIPTION', 'ITEM DESCRIPTION'],
        'IMEI':               ['IMEI', 'IMEI NUMBER', 'IMEI NO', 'SERIAL', 'SERIAL NO'],
        'QTY':                ['QTY', 'QUANTITY', 'UNITS', 'NOS', 'COUNT', 'NO OF UNITS'],
        'Financier':          ['FINANCIER', 'LENDER', 'FINANCE COMPANY', 'FINANCER', 'FINANCIER NAME'],
        'Delivery Order No':  ['DELIVERY ORDER NO', 'DO NO', 'DO NUMBER', 'DELIVERY NO', 'DELIVERY ORDER', 'DO'],
        'MOP':                ['MOP', 'MODE OF PAYMENT', 'PAYMENT MODE', 'PAY MODE'],
        'Sold Price':         ['SOLD PRICE', 'SELLING PRICE', 'SALE PRICE', 'PRICE', 'AMOUNT', 'NET AMOUNT', 'SELL PRICE'],
        'EMI':                ['EMI', 'EMI AMOUNT', 'MONTHLY INSTALMENT', 'MONTHLY INSTALLMENT'],
    }

    # Build lookup: UPPER stripped alias → canonical name
    alias_lookup = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_lookup[alias.upper().strip()] = canonical

    # Strip whitespace from all actual columns
    stripped = {col: col.strip() for col in df.columns}
    df.rename(columns=stripped, inplace=True)

    # Build rename map for actual columns
    rename_map = {}
    for actual_col in list(df.columns):
        key = actual_col.upper().strip()
        if key in alias_lookup:
            canonical = alias_lookup[key]
            if actual_col != canonical:
                rename_map[actual_col] = canonical

    if rename_map:
        print(f"  Column rename map applied: {rename_map}")
        df.rename(columns=rename_map, inplace=True)

    # Drop the raw 'Finance' input column if it exists alongside 'Financier'
    # (the file has both; 'Finance' is a pre-computed flag we recalculate ourselves)
    if 'Finance' in df.columns and 'Financier' in df.columns:
        df.drop(columns=['Finance'], inplace=True)
        print("  Dropped raw 'Finance' column (will be recomputed from Financier)")

    # Deduplicate any remaining duplicate columns (keep first occurrence)
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
        print("  Deduplicated columns (kept first occurrence of each)")

    return df


def _find_header_row(input_path):
    """
    Scan the first 20 rows of the Excel file to find the actual header row.
    The Combined Sales Report export includes several metadata rows (title,
    blank rows, 'Taken By', 'Action Date') before the real column headers.
    Returns the 0-based row index of the header row.
    """
    RAW_SENTINEL_COLS = {'RBM', 'SLNO', 'SL NO', 'BRANCH', 'SOLD PRICE', 'FINANCIER', 'INVOICE NUMBER'}
    probe = pd.read_excel(input_path, header=None, nrows=20, engine='calamine')
    for row_idx, row in probe.iterrows():
        row_vals = {str(v).strip().upper() for v in row.values if pd.notna(v) and str(v).strip()}
        if row_vals & RAW_SENTINEL_COLS:
            print(f"  Auto-detected header row at index {row_idx}")
            return row_idx
    return 0  # fallback: assume first row is header


def clean_sales_report(input_path, output_path):
    print(f"Reading input sales report: {input_path}")

    # ── Auto-detect header row (handles metadata rows before actual headers) ──
    header_row = _find_header_row(input_path)
    df = pd.read_excel(input_path, header=header_row, engine='calamine')
    print(f"Original shape: {df.shape}")
    print(f"Detected columns: {list(df.columns)}")

    # ── Auto-normalize column names (case-insensitive, strip whitespace) ──────
    df = _normalize_columns(df)
    print(f"Normalized columns: {list(df.columns)}")

    # ── Validate required columns exist ───────────────────────────────────────
    REQUIRED_COLUMNS = ['RBM', 'Sold Price', 'Financier', 'Product', 'Branch']
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(
            f"Required column(s) not found after normalization: {missing}. "
            f"Detected columns in your file: {list(df.columns)}"
        )

    # ── 1. Remove RBM = GENERAL and RAJESH ───────────────────────────────────
    remove_rbm = {'GENERAL', 'RAJESH'}
    rbm_clean = df['RBM'].astype(str).str.strip().str.upper()
    df = df[~rbm_clean.isin(remove_rbm)]
    print(f"Shape after RBM filter (remove GENERAL/RAJESH): {df.shape}")

    # ── 2. Price filter: keep > 3000 OR significant returns (< -3000) ────────
    price = pd.to_numeric(df['Sold Price'], errors='coerce').fillna(0.0)
    df = df[(price > 3000) | (price < -3000)]
    print(f"Shape after price filter (>3000 or <-3000): {df.shape}")

    # ── 3. Remove E-commerce entries ─────────────────────────────────────────
    financier = df['Financier'].astype(str).str.strip().str.upper()
    ecommerce_mask = financier.str.contains('ECOMMERCE|E-COMMERCE', na=False)
    df = df[~ecommerce_mask]
    print(f"Shape after E-commerce filter: {df.shape}")

    # ── 4. Standardise Financier ──────────────────────────────────────────────
    financier = df['Financier'].astype(str).str.strip().str.upper()
    res_fin = pd.Series('aCash', index=financier.index)

    # Keep manual upload overrides from ClickHouse (they output exact strings)
    # This prevents them from being wiped to aCash, while raw ERP data (which says "Pine Lab EMI" etc.) 
    # will fall through to aCash because it doesn't match the exact 'PINELAB' string.
    res_fin = np.where(financier == 'PINELAB', 'Pine Lab EMI', res_fin)
    res_fin = np.where(financier == 'PAYTM', 'PAYTM', res_fin)
    res_fin = np.where(financier == 'BENOW', 'Benow', res_fin)

    res_fin = np.where(financier.str.contains('BAJAJ', na=False), 'BAJAJ FINANCE', res_fin)
    res_fin = np.where(financier.str.contains('HDB', na=False), 'HDB FINANCE', res_fin)
    res_fin = np.where(financier.str.contains('DMI', na=False), 'DMI FINANCE', res_fin)
    res_fin = np.where(financier.str.contains('IDFC', na=False), 'IDFC FIRST BANK LTD', res_fin)
    res_fin = np.where(financier.str.contains('TVS', na=False), 'TVS Credit', res_fin)
    res_fin = np.where(financier.str.contains('POONAWALLA|PFL', na=False), 'Poonawalla Fincorp', res_fin)
    
    # Remaining cash-equivalent financiers → aCash
    cash_pattern = 'DELHIVERY|OFFICIUM|COCHIN|CASH|ACASH|CO-OP|COOP|SOCIETY|THAYANOOR'
    res_fin = np.where(financier.str.contains(cash_pattern, na=False), 'aCash', res_fin)
    res_fin = np.where(financier.isin(['', 'NAN', 'NONE']), 'aCash', res_fin)
    df['Financier_Cleaned'] = res_fin

    # ── 5. Finance column ─────────────────────────────────────────────────────
    df['Finance_Cleaned'] = np.where(df['Financier_Cleaned'] == 'aCash', 'aCash', 'FIN')

    # ── 6. Category mapping & blacklist removal ───────────────────────────────
    prod = df['Product'].astype(str).str.strip().str.upper()
    prod_clean = prod.str.replace(' ', '', regex=False)
    
    res_cat = pd.Series('BANK EMI', index=prod.index)
    res_cat = np.where(prod.isin(['', 'NAN', 'UNKNOWN']) & res_fin.isin(['Pine Lab EMI', 'PAYTM', 'Benow']), 'BANK EMI', res_cat)
    res_cat = np.where(prod.str.contains('SMALL APPLIANCE', na=False, regex=False), 'SA', res_cat)
    res_cat = np.where(prod.str.contains('TABLET|TAB', na=False), 'MOBILE', res_cat)
    
    bl_clean_list = [bl.replace(' ', '') for bl in BLACKLIST_PRODUCTS]
    res_cat = np.where(prod_clean.isin(bl_clean_list), 'REMOVE', res_cat)
    res_cat = np.where(prod.isin(BLACKLIST_PRODUCTS), 'REMOVE', res_cat)
    
    mapped_cat = prod.map(PRODUCT_CATEGORY_MAP)
    res_cat = np.where(mapped_cat.notna(), mapped_cat, res_cat)
    
    df['Category_Cleaned'] = res_cat
    
    # User requirement: no product will be listed for BANK EMI, leave it as blank
    df['Product_Cleaned'] = np.where(res_cat == 'BANK EMI', '', prod)

    df = df[df['Category_Cleaned'] != 'REMOVE']
    print(f"Shape after category removal: {df.shape}")

    # ── 7. Store → Fin BDM / RBM / BDM / Zone lookup (authoritative from DATA.xlsx) ─
    store_upper = df['Branch'].astype(str).str.strip().str.upper()

    def _store_lookup(mapping, s):
        v = mapping.get(s)
        if v: return v
        for k, val in mapping.items():
            if s in k or k in s: return val
        return ''

    # Fin BDM
    res_fin_bdm = store_upper.map(STORE_FIN_BDM_MAP)
    missing = res_fin_bdm.isna()
    if missing.any():
        res_fin_bdm[missing] = store_upper[missing].apply(lambda s: _store_lookup(STORE_FIN_BDM_MAP, s) or '')
    df['Fin_BDM_Cleaned'] = res_fin_bdm.fillna('')

    # RBM override (use DATA.xlsx authoritative mapping)
    res_rbm = store_upper.map(STORE_RBM_MAP)
    missing = res_rbm.isna()
    if missing.any():
        res_rbm[missing] = store_upper[missing].apply(lambda s: _store_lookup(STORE_RBM_MAP, s) or '')
    rbm_override = res_rbm.fillna('')
    df['RBM'] = np.where(rbm_override != '', rbm_override, df['RBM'])

    # BDM override (use DATA.xlsx authoritative mapping)
    res_bdm = store_upper.map(STORE_BDM_MAP)
    missing = res_bdm.isna()
    if missing.any():
        res_bdm[missing] = store_upper[missing].apply(lambda s: _store_lookup(STORE_BDM_MAP, s) or '')
    bdm_override = res_bdm.fillna('')
    df['BDM'] = np.where(bdm_override != '', bdm_override, df['BDM'])

    # Zone: store-level first, then RBM fallback
    zone_mapped = store_upper.map(STORE_ZONE_MAP)
    rbm_upper = df['RBM'].astype(str).str.strip().str.upper()
    zone_rbm = np.where(rbm_upper.isin(['ALFAS','SAMEER','SAMEEM']), 'NORTH',
               np.where(rbm_upper.isin(['RENJITH','MAHESH','VAISAKH']), 'SOUTH', ''))
    df['Zone_Cleaned'] = zone_mapped.fillna(pd.Series(zone_rbm, index=zone_mapped.index))

    # ── 8. Price Range classification (11 slabs per spec) ────────────────────
    price = pd.to_numeric(df['Sold Price'], errors='coerce').fillna(0.0)
    bins   = [0, 10000, 15000, 20000, 30000, 40000, 50000, 75000, 100000, 150000, 200000, np.inf]
    labels = [
        'Below 10K', '10K to 15K', '15K to 20K', '20K to 30K',
        '30K to 40K', '40K to 50K', '50K to 75K', '75K to 100K',
        '100K to 150K', '150K to 200K', 'Above 200K'
    ]
    df['Price_Range_Cleaned'] = pd.cut(price, bins=bins, labels=labels, right=False).astype(str)

    # ── 9. Store → Fin BDM lookup ─────────────────────────────────────────────
    store = df['Branch'].astype(str).str.strip().str.upper()
    res_bdm = store.map(STORE_FIN_BDM_MAP)
    
    def fuzzy_bdm(s):
        for k, v in STORE_FIN_BDM_MAP.items():
            if s == k or s in k or k in s: return v
        return ''
    
    missing_bdm = res_bdm.isna()
    if missing_bdm.any():
        res_bdm[missing_bdm] = store[missing_bdm].apply(fuzzy_bdm)
    df['Fin_BDM_Cleaned'] = res_bdm.fillna('')

    # ── 10. Clean Delivery Order No ───────────────────────────────────────────
    do = df['Delivery Order No'].astype(str).str.strip().str.upper()
    do = do.str.replace(' ', '', regex=False)
    do = np.where(do.isin(['', 'NA', 'N/A', '-', '0', '0.0', 'NULL', 'NAN', 'NONE']), '', do)
    df['DO_Cleaned'] = do

    # ── 10a. Delivery Order Overrides ─────────────────────────────────────────
    do_clean = df['DO_Cleaned']

    # BAJAJ FINANCE: all DO numbers starting with B followed by digits (B24, B42, B43, B44, B45, etc.)
    # Using regex: starts with B then a digit
    import re as _re
    bajaj_mask = do_clean.str.match(r'^B\d', na=False)
    df.loc[bajaj_mask, 'Financier_Cleaned'] = 'BAJAJ FINANCE'
    df.loc[bajaj_mask, 'Finance_Cleaned']   = 'FIN'
    bajaj_overridden = bajaj_mask.sum()
    if bajaj_overridden:
        print(f"  BAJAJ DO override applied: {bajaj_overridden} rows set to BAJAJ FINANCE")

    # DMI FINANCE: DO numbers that contain 'DMI' anywhere (handles 0DMI..., DMI..., etc.)
    dmi_mask = do_clean.str.contains('DMI', na=False, regex=False)
    df.loc[dmi_mask, 'Financier_Cleaned'] = 'DMI FINANCE'
    df.loc[dmi_mask, 'Finance_Cleaned']   = 'FIN'
    dmi_overridden = dmi_mask.sum()
    if dmi_overridden:
        print(f"  DMI DO override applied: {dmi_overridden} rows set to DMI FINANCE")

    # IDFC FIRST BANK: DO starts with 10108
    idfc_mask = do_clean.str.startswith('10108', na=False)
    df.loc[idfc_mask, 'Financier_Cleaned'] = 'IDFC FIRST BANK LTD'
    df.loc[idfc_mask, 'Finance_Cleaned']   = 'FIN'
    idfc_overridden = idfc_mask.sum()
    if idfc_overridden:
        print(f"  IDFC DO override applied: {idfc_overridden} rows set to IDFC FIRST BANK LTD")

    # POONAWALLA FINCORP: DO starts with CDSK
    poonawalla_mask = do_clean.str.startswith('CDSK', na=False)
    df.loc[poonawalla_mask, 'Financier_Cleaned'] = 'Poonawalla Fincorp'
    df.loc[poonawalla_mask, 'Finance_Cleaned']   = 'FIN'
    poonawalla_overridden = poonawalla_mask.sum()
    if poonawalla_overridden:
        print(f"  Poonawalla DO override applied: {poonawalla_overridden} rows set to Poonawalla Fincorp")

    # TVS CREDIT: DO starts with 3022CD or 3023CD (or any 30xxCD pattern)
    tvs_mask = do_clean.str.startswith(('3022CD', '3023CD'), na=False)
    df.loc[tvs_mask, 'Financier_Cleaned'] = 'TVS Credit'
    df.loc[tvs_mask, 'Finance_Cleaned']   = 'FIN'
    tvs_overridden = tvs_mask.sum()
    if tvs_overridden:
        print(f"  TVS DO override applied: {tvs_overridden} rows set to TVS Credit")

    # HDB FINANCE: long numeric DO numbers (8+ digits, all numeric)
    # These are DOs that start with a digit but didn't match IDFC (10108) prefix.
    # Any DO number that is purely numeric and not caught by IDFC → HDB FINANCE.
    hdb_mask = (
        do_clean.str.match(r'^\d+$', na=False)   # purely numeric
        & ~idfc_mask                              # not already mapped to IDFC
        & (do_clean.str.len() >= 8)              # long enough to be a real finance DO
    )
    df.loc[hdb_mask, 'Financier_Cleaned'] = 'HDB FINANCE'
    df.loc[hdb_mask, 'Finance_Cleaned']   = 'FIN'
    hdb_overridden = hdb_mask.sum()
    if hdb_overridden:
        print(f"  HDB DO override applied: {hdb_overridden} rows set to HDB FINANCE")

    print(f"Final output shape will be: ({len(df)}, 30)")

    out_df = pd.DataFrame()
    out_df['Slno']              = df['Slno']
    out_df['STore']             = df['Branch']
    out_df['Fin BDM']           = df['Fin_BDM_Cleaned']
    out_df['RBM']               = df['RBM'].apply(lambda x: str(x).strip() if pd.notna(x) else x)
    out_df['BDM']               = df['BDM']
    out_df['Date']              = df['Date']
    out_df['Month']             = df['Time']       
    out_df['Invoice Number']    = df['Invoice Number']
    out_df['Customer Name']     = df['Customer Name']
    out_df['Customer Mobile']   = df['Customer Mobile']
    out_df['Pincode']           = df['Pincode']
    out_df['Staff']             = df['Staff']
    out_df['Product']           = df['Product_Cleaned']
    out_df['Category']          = df['Category_Cleaned']
    out_df['Brand']             = df['Brand']
    out_df['Item Group']        = df['Item Group']
    out_df['Item Category']     = df['Item Category']
    out_df['Item Code']         = df['Item Code']
    out_df['Item Name']         = df['Item Name']
    out_df['IMEI']              = df['IMEI']
    # If Sold Price is negative (return), QTY must also be negative
    qty_raw   = pd.to_numeric(df['QTY'],       errors='coerce').fillna(0)
    price_raw = pd.to_numeric(df['Sold Price'], errors='coerce').fillna(0)
    out_df['QTY'] = np.where(price_raw < 0, -qty_raw.abs(), qty_raw.abs())

    out_df['Financier']         = df['Financier_Cleaned']
    out_df['Finance']           = df['Finance_Cleaned']
    out_df['Delivery Order No'] = df['DO_Cleaned']
    out_df['MOP']               = df['MOP']
    out_df['Sold Price']        = df['Sold Price']
    out_df['EMI']               = df['EMI']
    out_df['Price Range']       = df['Price_Range_Cleaned']
    out_df['Price']             = df['Sold Price']   
    out_df['Zone']              = df['Zone_Cleaned']

    # ── 12. Write plain Excel – Sheet1 (no template dependency) ──────────────
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        out_df.to_excel(writer, sheet_name='Sheet1', index=False)

    print(f"Cleaned report written to: {output_path} ({len(out_df)} rows, Sheet1)")


if __name__ == "__main__":
    clean_sales_report(
        r"c:\Users\rahul_myg\Downloads\21-07-2026_10_10_39_569187_CombinedSalesReport.xlsx",
        r"c:\Users\rahul_myg\Downloads\FINANCE PORTAL\data\test_cleaned.xlsx"
    )
