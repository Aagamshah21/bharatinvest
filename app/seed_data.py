import json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.database import engine, SessionLocal, Base, run_db_migrations
from app.models import (
    User, Instrument, Holding, Order, Trade, Wallet, LedgerEntry,
    Watchlist, WatchlistItem, SIP, OAuthClientApp, SystemSettings
)
from app.services.auth_service import hash_password
from app.services.price_simulator import generate_historical_data_if_needed

RAW_INSTRUMENTS = [
    # STOCKS (42)
    {"symbol": "RELIANCE", "name": "Reliance Industries Ltd", "isin": "INE002A01018", "category": "STOCK", "price": 2487.30, "prev_close": 2467.60, "open": 2470.00, "high": 2495.00, "low": 2462.00, "vol": 3450000, "w52_h": 3024.90, "w52_l": 2220.00, "mcap": 1682450.0, "pe": 26.4, "div": 0.45, "circuit": 10.0, "sector": "Energy"},
    {"symbol": "TCS", "name": "Tata Consultancy Services Ltd", "isin": "INE467B01029", "category": "STOCK", "price": 3850.50, "prev_close": 3865.70, "open": 3860.00, "high": 3885.00, "low": 3840.00, "vol": 1820000, "w52_h": 4585.00, "w52_l": 3310.00, "mcap": 1392100.0, "pe": 29.8, "div": 1.25, "circuit": 10.0, "sector": "IT"},
    {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "isin": "INE040A01034", "category": "STOCK", "price": 1642.10, "prev_close": 1635.00, "open": 1638.00, "high": 1650.00, "low": 1630.00, "vol": 6200000, "w52_h": 1794.00, "w52_l": 1363.00, "mcap": 1251800.0, "pe": 19.2, "div": 1.10, "circuit": 10.0, "sector": "Banking"},
    {"symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "isin": "INE090A01021", "category": "STOCK", "price": 1210.40, "prev_close": 1202.00, "open": 1205.00, "high": 1218.00, "low": 1198.00, "vol": 4500000, "w52_h": 1362.00, "w52_l": 908.00, "mcap": 851400.0, "pe": 17.6, "div": 0.85, "circuit": 10.0, "sector": "Banking"},
    {"symbol": "INFY", "name": "Infosys Ltd", "isin": "INE009A01021", "category": "STOCK", "price": 1512.80, "prev_close": 1520.00, "open": 1515.00, "high": 1530.00, "low": 1502.00, "vol": 2900000, "w52_h": 1975.00, "w52_l": 1355.00, "mcap": 628400.0, "pe": 24.5, "div": 2.20, "circuit": 10.0, "sector": "IT"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel Ltd", "isin": "INE397D01024", "category": "STOCK", "price": 1365.25, "prev_close": 1350.00, "open": 1355.00, "high": 1372.00, "low": 1348.00, "vol": 3100000, "w52_h": 1779.00, "w52_l": 905.00, "mcap": 812500.0, "pe": 42.1, "div": 0.60, "circuit": 10.0, "sector": "Telecom"},
    {"symbol": "ITC", "name": "ITC Ltd", "isin": "INE154A01025", "category": "STOCK", "price": 448.60, "prev_close": 445.00, "open": 446.00, "high": 451.00, "low": 444.00, "vol": 7800000, "w52_h": 528.00, "w52_l": 399.00, "mcap": 560200.0, "pe": 27.2, "div": 3.10, "circuit": 10.0, "sector": "Consumer Goods"},
    {"symbol": "SBIN", "name": "State Bank of India", "isin": "INE062A01020", "category": "STOCK", "price": 815.70, "prev_close": 810.00, "open": 812.00, "high": 822.00, "low": 808.00, "vol": 8900000, "w52_h": 912.00, "w52_l": 560.00, "mcap": 728000.0, "pe": 10.4, "div": 1.68, "circuit": 10.0, "sector": "Banking"},
    {"symbol": "LTIM", "name": "LTIMindtree Ltd", "isin": "INE214T01019", "category": "STOCK", "price": 5420.00, "prev_close": 5450.00, "open": 5440.00, "high": 5480.00, "low": 5390.00, "vol": 420000, "w52_h": 6540.00, "w52_l": 4520.00, "mcap": 160400.0, "pe": 34.1, "div": 1.20, "circuit": 10.0, "sector": "IT"},
    {"symbol": "LT", "name": "Larsen & Toubro Ltd", "isin": "INE018A01030", "category": "STOCK", "price": 3610.00, "prev_close": 3590.00, "open": 3600.00, "high": 3645.00, "low": 3580.00, "vol": 1150000, "w52_h": 3919.00, "w52_l": 2850.00, "mcap": 496300.0, "pe": 36.8, "div": 0.80, "circuit": 10.0, "sector": "Infrastructure"},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever Ltd", "isin": "INE030A01027", "category": "STOCK", "price": 2380.00, "prev_close": 2395.00, "open": 2390.00, "high": 2405.00, "low": 2372.00, "vol": 1420000, "w52_h": 3034.00, "w52_l": 2170.00, "mcap": 559200.0, "pe": 53.2, "div": 1.75, "circuit": 10.0, "sector": "Consumer Goods"},
    {"symbol": "AXISBANK", "name": "Axis Bank Ltd", "isin": "INE238A01034", "category": "STOCK", "price": 1182.50, "prev_close": 1175.00, "open": 1178.00, "high": 1190.00, "low": 1170.00, "vol": 3800000, "w52_h": 1339.00, "w52_l": 932.00, "mcap": 365400.0, "pe": 14.1, "div": 0.10, "circuit": 10.0, "sector": "Banking"},
    {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank Ltd", "isin": "INE237A01028", "category": "STOCK", "price": 1765.00, "prev_close": 1758.00, "open": 1760.00, "high": 1775.00, "low": 1750.00, "vol": 2100000, "w52_h": 1925.00, "w52_l": 1543.00, "mcap": 351000.0, "pe": 21.5, "div": 0.11, "circuit": 10.0, "sector": "Banking"},
    {"symbol": "TATAMOTORS", "name": "Tata Motors Ltd", "isin": "INE155A01022", "category": "STOCK", "price": 954.20, "prev_close": 948.00, "open": 950.00, "high": 962.00, "low": 945.00, "vol": 5400000, "w52_h": 1179.00, "w52_l": 612.00, "mcap": 317200.0, "pe": 10.8, "div": 0.63, "circuit": 10.0, "sector": "Automobile"},
    {"symbol": "M&M", "name": "Mahindra & Mahindra Ltd", "isin": "INE101A01026", "category": "STOCK", "price": 2840.00, "prev_close": 2810.00, "open": 2820.00, "high": 2865.00, "low": 2805.00, "vol": 2300000, "w52_h": 3222.00, "w52_l": 1450.00, "mcap": 353100.0, "pe": 28.5, "div": 0.75, "circuit": 10.0, "sector": "Automobile"},
    {"symbol": "MARUTI", "name": "Maruti Suzuki India Ltd", "isin": "INE585B01010", "category": "STOCK", "price": 12450.00, "prev_close": 12380.00, "open": 12400.00, "high": 12520.00, "low": 12350.00, "vol": 310000, "w52_h": 13680.00, "w52_l": 9735.00, "mcap": 391200.0, "pe": 28.1, "div": 1.00, "circuit": 10.0, "sector": "Automobile"},
    {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical Industries Ltd", "isin": "INE044A01036", "category": "STOCK", "price": 1725.00, "prev_close": 1710.00, "open": 1715.00, "high": 1735.00, "low": 1708.00, "vol": 1650000, "w52_h": 1960.00, "w52_l": 1115.00, "mcap": 413800.0, "pe": 39.4, "div": 0.78, "circuit": 10.0, "sector": "Pharma"},
    {"symbol": "TITAN", "name": "Titan Company Ltd", "isin": "INE280A01028", "category": "STOCK", "price": 3420.00, "prev_close": 3440.00, "open": 3435.00, "high": 3450.00, "low": 3400.00, "vol": 920000, "w52_h": 3886.00, "w52_l": 3055.00, "mcap": 303600.0, "pe": 86.2, "div": 0.32, "circuit": 10.0, "sector": "Consumer Goods"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance Ltd", "isin": "INE296A01024", "category": "STOCK", "price": 6980.00, "prev_close": 7020.00, "open": 7000.00, "high": 7050.00, "low": 6940.00, "vol": 850000, "w52_h": 8192.00, "w52_l": 6375.00, "mcap": 431000.0, "pe": 29.5, "div": 0.52, "circuit": 10.0, "sector": "Financial Services"},
    {"symbol": "BAJAJFINSV", "name": "Bajaj Finserv Ltd", "isin": "INE918I01026", "category": "STOCK", "price": 1820.00, "prev_close": 1810.00, "open": 1815.00, "high": 1835.00, "low": 1805.00, "vol": 1200000, "w52_h": 2028.00, "w52_l": 1419.00, "mcap": 290200.0, "pe": 35.0, "div": 0.14, "circuit": 10.0, "sector": "Financial Services"},
    {"symbol": "NTPC", "name": "NTPC Ltd", "isin": "INE733E01010", "category": "STOCK", "price": 412.30, "prev_close": 408.00, "open": 409.00, "high": 415.00, "low": 407.00, "vol": 9200000, "w52_h": 448.00, "w52_l": 230.00, "mcap": 399800.0, "pe": 19.5, "div": 1.80, "circuit": 10.0, "sector": "Energy"},
    {"symbol": "POWERGRID", "name": "Power Grid Corporation of India Ltd", "isin": "INE752E01010", "category": "STOCK", "price": 332.50, "prev_close": 330.00, "open": 331.00, "high": 335.00, "low": 328.00, "vol": 6700000, "w52_h": 366.00, "w52_l": 198.00, "mcap": 309200.0, "pe": 19.1, "div": 3.40, "circuit": 10.0, "sector": "Energy"},
    {"symbol": "ONGC", "name": "Oil & Natural Gas Corporation Ltd", "isin": "INE213A01029", "category": "STOCK", "price": 288.40, "prev_close": 285.00, "open": 286.00, "high": 291.00, "low": 284.00, "vol": 11200000, "w52_h": 344.00, "w52_l": 182.00, "mcap": 362800.0, "pe": 8.7, "div": 4.20, "circuit": 10.0, "sector": "Energy"},
    {"symbol": "TATASTEEL", "name": "Tata Steel Ltd", "isin": "INE081A01020", "category": "STOCK", "price": 158.20, "prev_close": 159.50, "open": 159.00, "high": 160.50, "low": 157.00, "vol": 14500000, "w52_h": 184.60, "w52_l": 118.00, "mcap": 197500.0, "pe": 48.0, "div": 2.20, "circuit": 10.0, "sector": "Metals"},
    {"symbol": "JSWSTEEL", "name": "JSW Steel Ltd", "isin": "INE019A01038", "category": "STOCK", "price": 985.00, "prev_close": 980.00, "open": 982.00, "high": 992.00, "low": 975.00, "vol": 1850000, "w52_h": 1048.00, "w52_l": 745.00, "mcap": 240800.0, "pe": 28.0, "div": 0.74, "circuit": 10.0, "sector": "Metals"},
    {"symbol": "ADANIENT", "name": "Adani Enterprises Ltd", "isin": "INE423A01024", "category": "STOCK", "price": 3120.00, "prev_close": 3150.00, "open": 3140.00, "high": 3170.00, "low": 3090.00, "vol": 1950000, "w52_h": 3743.00, "w52_l": 2142.00, "mcap": 355600.0, "pe": 102.0, "div": 0.04, "circuit": 10.0, "sector": "Metals"},
    {"symbol": "ADANIPORTS", "name": "Adani Ports & SEZ Ltd", "isin": "INE742F01042", "category": "STOCK", "price": 1410.00, "prev_close": 1395.00, "open": 1400.00, "high": 1422.00, "low": 1390.00, "vol": 2800000, "w52_h": 1607.00, "w52_l": 754.00, "mcap": 304600.0, "pe": 32.5, "div": 0.42, "circuit": 10.0, "sector": "Infrastructure"},
    {"symbol": "COALINDIA", "name": "Coal India Ltd", "isin": "INE522F01014", "category": "STOCK", "price": 492.00, "prev_close": 488.00, "open": 490.00, "high": 496.00, "low": 486.00, "vol": 6400000, "w52_h": 543.00, "w52_l": 282.00, "mcap": 303200.0, "pe": 8.1, "div": 5.20, "circuit": 10.0, "sector": "Energy"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints Ltd", "isin": "INE021A01026", "category": "STOCK", "price": 2980.00, "prev_close": 3010.00, "open": 3000.00, "high": 3020.00, "low": 2965.00, "vol": 1050000, "w52_h": 3422.00, "w52_l": 2670.00, "mcap": 285800.0, "pe": 52.0, "div": 1.10, "circuit": 10.0, "sector": "Consumer Goods"},
    {"symbol": "ULTRACEMCO", "name": "UltraTech Cement Ltd", "isin": "INE481G01011", "category": "STOCK", "price": 11200.00, "prev_close": 11150.00, "open": 11180.00, "high": 11280.00, "low": 11120.00, "vol": 280000, "w52_h": 12140.00, "w52_l": 7940.00, "mcap": 330200.0, "pe": 44.0, "div": 0.62, "circuit": 10.0, "sector": "Infrastructure"},
    {"symbol": "WIPRO", "name": "Wipro Ltd", "isin": "INE075A01022", "category": "STOCK", "price": 535.40, "prev_close": 532.00, "open": 533.00, "high": 539.00, "low": 530.00, "vol": 4100000, "w52_h": 585.00, "w52_l": 375.00, "mcap": 280100.0, "pe": 24.2, "div": 0.19, "circuit": 10.0, "sector": "IT"},
    {"symbol": "HCLTECH", "name": "HCL Technologies Ltd", "isin": "INE860A01027", "category": "STOCK", "price": 1780.00, "prev_close": 1765.00, "open": 1770.00, "high": 1795.00, "low": 1760.00, "vol": 2200000, "w52_h": 1886.00, "w52_l": 1210.00, "mcap": 482900.0, "pe": 28.6, "div": 2.90, "circuit": 10.0, "sector": "IT"},
    {"symbol": "NESTLEIND", "name": "Nestle India Ltd", "isin": "INE239A01016", "category": "STOCK", "price": 2420.00, "prev_close": 2435.00, "open": 2430.00, "high": 2445.00, "low": 2410.00, "vol": 520000, "w52_h": 2777.00, "w52_l": 2145.00, "mcap": 233300.0, "pe": 72.5, "div": 1.30, "circuit": 10.0, "sector": "Consumer Goods"},
    {"symbol": "GRASIM", "name": "Grasim Industries Ltd", "isin": "INE047A01021", "category": "STOCK", "price": 2680.00, "prev_close": 2660.00, "open": 2665.00, "high": 2700.00, "low": 2650.00, "vol": 820000, "w52_h": 2875.00, "w52_l": 1845.00, "mcap": 182400.0, "pe": 31.0, "div": 0.35, "circuit": 10.0, "sector": "Infrastructure"},
    {"symbol": "TECHM", "name": "Tech Mahindra Ltd", "isin": "INE669C01036", "category": "STOCK", "price": 1620.00, "prev_close": 1605.00, "open": 1610.00, "high": 1635.00, "low": 1600.00, "vol": 1450000, "w52_h": 1759.00, "w52_l": 1130.00, "mcap": 158400.0, "pe": 41.5, "div": 1.70, "circuit": 10.0, "sector": "IT"},
    {"symbol": "CIPLA", "name": "Cipla Ltd", "isin": "INE059A01026", "category": "STOCK", "price": 1615.00, "prev_close": 1625.00, "open": 1620.00, "high": 1632.00, "low": 1608.00, "vol": 1100000, "w52_h": 1702.00, "w52_l": 1125.00, "mcap": 130400.0, "pe": 28.4, "div": 0.80, "circuit": 10.0, "sector": "Pharma"},
    {"symbol": "DRREDDY", "name": "Dr. Reddy's Laboratories Ltd", "isin": "INE089A01023", "category": "STOCK", "price": 6680.00, "prev_close": 6650.00, "open": 6660.00, "high": 6720.00, "low": 6630.00, "vol": 420000, "w52_h": 7100.00, "w52_l": 5210.00, "mcap": 111400.0, "pe": 20.8, "div": 0.60, "circuit": 10.0, "sector": "Pharma"},
    {"symbol": "HEROMOTOCO", "name": "Hero MotoCorp Ltd", "isin": "INE158A01026", "category": "STOCK", "price": 5640.00, "prev_close": 5600.00, "open": 5610.00, "high": 5680.00, "low": 5580.00, "vol": 480000, "w52_h": 5890.00, "w52_l": 2980.00, "mcap": 112700.0, "pe": 25.1, "div": 2.40, "circuit": 10.0, "sector": "Automobile"},
    {"symbol": "EICHERMOT", "name": "Eicher Motors Ltd", "isin": "INE066A01021", "category": "STOCK", "price": 4890.00, "prev_close": 4850.00, "open": 4860.00, "high": 4920.00, "low": 4840.00, "vol": 510000, "w52_h": 5105.00, "w52_l": 3160.00, "mcap": 133900.0, "pe": 33.2, "div": 1.05, "circuit": 10.0, "sector": "Automobile"},
    {"symbol": "DIVISLAB", "name": "Divi's Laboratories Ltd", "isin": "INE361B01024", "category": "STOCK", "price": 5180.00, "prev_close": 5210.00, "open": 5200.00, "high": 5240.00, "low": 5150.00, "vol": 380000, "w52_h": 5485.00, "w52_l": 3315.00, "mcap": 137500.0, "pe": 71.0, "div": 0.58, "circuit": 10.0, "sector": "Pharma"},
    {"symbol": "BEL", "name": "Bharat Electronics Ltd", "isin": "INE263A01024", "category": "STOCK", "price": 292.50, "prev_close": 288.00, "open": 290.00, "high": 295.00, "low": 287.00, "vol": 12800000, "w52_h": 340.00, "w52_l": 124.00, "mcap": 213800.0, "pe": 47.0, "div": 0.75, "circuit": 10.0, "sector": "Capital Goods"},
    {"symbol": "TRENT", "name": "Trent Ltd", "isin": "INE849A01020", "category": "STOCK", "price": 7650.00, "prev_close": 7580.00, "open": 7600.00, "high": 7720.00, "low": 7550.00, "vol": 950000, "w52_h": 8345.00, "w52_l": 1960.00, "mcap": 271900.0, "pe": 165.0, "div": 0.05, "circuit": 10.0, "sector": "Consumer Goods"},

    # REITs (2)
    {"symbol": "EMBASSY", "name": "Embassy Office Parks REIT", "isin": "INE041025011", "category": "REIT", "price": 365.40, "prev_close": 362.00, "open": 363.00, "high": 368.00, "low": 361.00, "vol": 850000, "w52_h": 412.00, "w52_l": 298.00, "mcap": 34600.0, "pe": 22.4, "div": 5.80, "circuit": 5.0, "sector": "Real Estate"},
    {"symbol": "MINDSPACE", "name": "Mindspace Business Parks REIT", "isin": "INE0CCU25019", "category": "REIT", "price": 348.00, "prev_close": 345.00, "open": 346.00, "high": 351.00, "low": 344.00, "vol": 420000, "w52_h": 385.00, "w52_l": 305.00, "mcap": 20600.0, "pe": 24.1, "div": 5.50, "circuit": 5.0, "sector": "Real Estate"},

    # InvITs (2)
    {"symbol": "PGINVIT", "name": "POWERGRID Infrastructure Investment Trust", "isin": "INE0B2623019", "category": "INVIT", "price": 102.50, "prev_close": 101.80, "open": 102.00, "high": 103.20, "low": 101.50, "vol": 1250000, "w52_h": 122.00, "w52_l": 92.00, "mcap": 9300.0, "pe": 12.5, "div": 11.20, "circuit": 5.0, "sector": "Infrastructure"},
    {"symbol": "INDIGRID", "name": "India Grid Trust", "isin": "INE219X23014", "category": "INVIT", "price": 142.80, "prev_close": 141.50, "open": 142.00, "high": 143.50, "low": 141.00, "vol": 680000, "w52_h": 155.00, "w52_l": 126.00, "mcap": 10700.0, "pe": 14.2, "div": 9.80, "circuit": 5.0, "sector": "Infrastructure"},

    # ETFs (2)
    {"symbol": "NIFTYBEES", "name": "Nippon India ETF Nifty BeES", "isin": "INF204KB14I2", "category": "ETF", "price": 268.40, "prev_close": 267.10, "open": 267.50, "high": 269.20, "low": 266.80, "vol": 4200000, "w52_h": 288.00, "w52_l": 212.00, "mcap": 28500.0, "pe": 23.5, "div": 0.80, "circuit": 5.0, "sector": "Index ETF"},
    {"symbol": "GOLDBEES", "name": "Nippon India ETF Gold BeES", "isin": "INF204KB17I5", "category": "ETF", "price": 64.20, "prev_close": 63.80, "open": 64.00, "high": 64.50, "low": 63.70, "vol": 6800000, "w52_h": 68.50, "w52_l": 51.20, "mcap": 14200.0, "pe": 0.0, "div": 0.00, "circuit": 5.0, "sector": "Commodity ETF"},

    # Bonds (2)
    {"symbol": "SGB2031", "name": "Sovereign Gold Bond 2031 Series IV", "isin": "IN0020210236", "category": "BOND", "price": 7250.00, "prev_close": 7200.00, "open": 7210.00, "high": 7280.00, "low": 7190.00, "vol": 15000, "w52_h": 7650.00, "w52_l": 5800.00, "mcap": 5000.0, "pe": 0.0, "div": 2.50, "circuit": 5.0, "sector": "Government Bond"},
    {"symbol": "GOI754", "name": "7.54% Government of India Loan 2036", "isin": "IN0020220011", "category": "BOND", "price": 1045.00, "prev_close": 1042.00, "open": 1043.00, "high": 1047.00, "low": 1041.00, "vol": 35000, "w52_h": 1065.00, "w52_l": 985.00, "mcap": 12000.0, "pe": 0.0, "div": 7.54, "circuit": 5.0, "sector": "Government Bond"}
]

def seed_database(db: Session = None):
    Base.metadata.create_all(bind=engine)
    run_db_migrations(engine)
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        # 1. System Settings
        default_settings = [
            ("market_open", "true"),
            ("simulate_outage", "false"),
            ("slow_mode", "false"),
            ("flaky_mode", "false")
        ]
        for k, v in default_settings:
            st = db.query(SystemSettings).filter(SystemSettings.key == k).first()
            if not st:
                db.add(SystemSettings(key=k, value=v))
        db.commit()

        # 2. Instruments
        inst_map = {}
        for item in RAW_INSTRUMENTS:
            inst = db.query(Instrument).filter(Instrument.symbol == item["symbol"]).first()
            if not inst:
                inst = Instrument(
                    symbol=item["symbol"],
                    name=item["name"],
                    isin=item["isin"],
                    category=item["category"],
                    segment="EQ",
                    current_price=item["price"],
                    prev_close=item["prev_close"],
                    open_price=item["open"],
                    high_price=item["high"],
                    low_price=item["low"],
                    volume=item["vol"],
                    week_52_high=item["w52_h"],
                    week_52_low=item["w52_l"],
                    market_cap=item["mcap"],
                    pe_ratio=item["pe"],
                    div_yield=item["div"],
                    circuit_limit_pct=item["circuit"],
                    sector=item["sector"]
                )
                db.add(inst)
                db.commit()
                db.refresh(inst)
            inst_map[item["symbol"]] = inst

        # 3. Seed Demo Users
        aarav = db.query(User).filter(User.mobile == "9000000001").first()
        if not aarav:
            aarav = User(
                mobile="9000000001",
                password_hash=hash_password("demo123"),
                full_name="Aarav Mehta",
                client_code="BI10021",
                email="aarav@example.com",
                pan_masked="ABCDE1234F",
                demat_account="1208160012345678"
            )
            db.add(aarav)
            db.commit()
            db.refresh(aarav)
            db.add(Wallet(user_id=aarav.id, balance=50000.00))
            db.add(LedgerEntry(
                user_id=aarav.id, amount=50000.00, type="DEPOSIT",
                description="Initial Account Funding via UPI", balance_after=50000.00
            ))
            db.commit()

        priya = db.query(User).filter(User.mobile == "9000000002").first()
        if not priya:
            priya = User(
                mobile="9000000002",
                password_hash=hash_password("demo123"),
                full_name="Priya Nair",
                client_code="BI10022",
                email="priya@example.com",
                pan_masked="PQRST5678U",
                demat_account="1208160087654321"
            )
            db.add(priya)
            db.commit()
            db.refresh(priya)
            db.add(Wallet(user_id=priya.id, balance=35000.00))
            db.add(LedgerEntry(
                user_id=priya.id, amount=35000.00, type="DEPOSIT",
                description="Initial Account Funding via NetBanking", balance_after=35000.00
            ))
            db.commit()

        # 4. Aarav Holdings (9 holdings)
        aarav_holdings_seed = [
            ("RELIANCE", 5, 2460.00),
            ("TCS", 10, 3720.00),
            ("HDFCBANK", 15, 1610.00),
            ("ITC", 40, 420.00),
            ("INFY", 8, 1480.00),
            ("SBIN", 25, 780.00),
            ("NIFTYBEES", 50, 240.00),
            ("EMBASSY", 30, 340.00),
            ("TATAMOTORS", 20, 920.00),
        ]
        for sym, qty, avg_price in aarav_holdings_seed:
            inst = inst_map[sym]
            h = db.query(Holding).filter(Holding.user_id == aarav.id, Holding.instrument_id == inst.id).first()
            if not h:
                db.add(Holding(user_id=aarav.id, instrument_id=inst.id, quantity=qty, average_price=avg_price))

        # 5. Priya Holdings (7 holdings)
        priya_holdings_seed = [
            ("ICICIBANK", 20, 1150.00),
            ("BHARTIARTL", 15, 1310.00),
            ("PGINVIT", 100, 98.00),
            ("GOLDBEES", 40, 58.00),
            ("SUNPHARMA", 12, 1680.00),
            ("MARUTI", 2, 12100.00),
            ("TITAN", 5, 3350.00),
        ]
        for sym, qty, avg_price in priya_holdings_seed:
            inst = inst_map[sym]
            h = db.query(Holding).filter(Holding.user_id == priya.id, Holding.instrument_id == inst.id).first()
            if not h:
                db.add(Holding(user_id=priya.id, instrument_id=inst.id, quantity=qty, average_price=avg_price))

        db.commit()

        # 6. Seed SIP for Aarav
        niftybees_inst = inst_map["NIFTYBEES"]
        existing_sip = db.query(SIP).filter(SIP.user_id == aarav.id).first()
        if not existing_sip:
            db.add(SIP(
                user_id=aarav.id,
                instrument_id=niftybees_inst.id,
                monthly_amount=5000.00,
                day_of_month=5,
                status="ACTIVE",
                last_executed_at=datetime.now(timezone.utc) - timedelta(days=25)
            ))
            db.commit()

        # 7. Seed Watchlists for Aarav & Priya
        w1 = db.query(Watchlist).filter(Watchlist.user_id == aarav.id, Watchlist.name == "Main Watchlist").first()
        if not w1:
            w1 = Watchlist(user_id=aarav.id, name="Main Watchlist")
            db.add(w1)
            db.commit()
            db.refresh(w1)
            for sym in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "NIFTYBEES"]:
                db.add(WatchlistItem(watchlist_id=w1.id, instrument_id=inst_map[sym].id))
            db.commit()

        # 8. Seed Registered OAuth App
        app_obj = db.query(OAuthClientApp).filter(OAuthClientApp.app_id == "portfolio-aggregator").first()
        if not app_obj:
            db.add(OAuthClientApp(
                app_name="Portfolio Aggregator",
                app_id="portfolio-aggregator",
                app_secret="bi-demo-secret",
                allowed_callback_urls="http://localhost:3000/callback,http://localhost:8000/callback,http://127.0.0.1:8000/callback,http://127.0.0.1:3000/callback"
            ))
            db.commit()

        # 9. Seed 3 Months Historical Orders and Trades for Aarav & Priya
        if db.query(Order).count() == 0:
            now = datetime.now(timezone.utc)
            sample_trades = [
                (aarav.id, "RELIANCE", "BUY", "DELIVERY", 5, 2460.00, now - timedelta(days=60)),
                (aarav.id, "TCS", "BUY", "DELIVERY", 10, 3720.00, now - timedelta(days=45)),
                (aarav.id, "HDFCBANK", "BUY", "DELIVERY", 15, 1610.00, now - timedelta(days=30)),
                (aarav.id, "ITC", "BUY", "DELIVERY", 40, 420.00, now - timedelta(days=28)),
                (aarav.id, "INFY", "BUY", "DELIVERY", 8, 1480.00, now - timedelta(days=26)),
                (aarav.id, "SBIN", "BUY", "DELIVERY", 25, 780.00, now - timedelta(days=25)),
                (aarav.id, "NIFTYBEES", "BUY", "DELIVERY", 50, 240.00, now - timedelta(days=22)),
                (aarav.id, "EMBASSY", "BUY", "DELIVERY", 30, 340.00, now - timedelta(days=20)),
                (aarav.id, "TATAMOTORS", "BUY", "DELIVERY", 20, 920.00, now - timedelta(days=15)),
                (priya.id, "ICICIBANK", "BUY", "DELIVERY", 20, 1150.00, now - timedelta(days=50)),
                (priya.id, "BHARTIARTL", "BUY", "DELIVERY", 15, 1310.00, now - timedelta(days=45)),
                (priya.id, "PGINVIT", "BUY", "DELIVERY", 100, 98.00, now - timedelta(days=42)),
                (priya.id, "GOLDBEES", "BUY", "DELIVERY", 40, 58.00, now - timedelta(days=40)),
                (priya.id, "SUNPHARMA", "BUY", "DELIVERY", 12, 1680.00, now - timedelta(days=35)),
                (priya.id, "MARUTI", "BUY", "DELIVERY", 2, 12100.00, now - timedelta(days=30)),
                (priya.id, "TITAN", "BUY", "DELIVERY", 5, 3350.00, now - timedelta(days=25)),
            ]
            for uid, sym, t_type, p_type, qty, pr, dt_traded in sample_trades:
                inst = inst_map[sym]
                o = Order(
                    user_id=uid,
                    instrument_id=inst.id,
                    transaction_type=t_type,
                    order_type="MARKET",
                    product_type=p_type,
                    quantity=qty,
                    price=pr,
                    executed_price=pr,
                    status="COMPLETE",
                    created_at=dt_traded,
                    updated_at=dt_traded
                )
                db.add(o)
                db.commit()
                db.refresh(o)

                tr = Trade(
                    user_id=uid,
                    order_id=o.id,
                    instrument_id=inst.id,
                    transaction_type=t_type,
                    quantity=qty,
                    price=pr,
                    executed_at=dt_traded
                )
                db.add(tr)
            db.commit()

        # 10. Generate 5-year Price History
        generate_historical_data_if_needed(db)

    finally:
        if should_close:
            db.close()

