import streamlit as st
import pandas as pd
import requests
import time
import io
from datetime import date

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ===============================
# Fetch MAUDE data (OpenFDA)
# ===============================
def fetch_maude_reports(product_code, max_records=None, start_year=None, end_year=None):
    base_url = "https://api.fda.gov/device/event.json"
    all_results = []
    skip = 0
    limit = 100

    if isinstance(max_records, str):
        if max_records.lower() == "all":
            max_records = float("inf")
        else:
            try:
                max_records = int(max_records)
            except ValueError:
                max_records = 100

    if max_records is None:
        max_records = float("inf")

    date_filter = ""
    if start_year and end_year:
        date_filter = f" AND date_received:[{start_year}0101 TO {end_year}1231]"
    elif start_year and not end_year:
        date_filter = f" AND date_received:[{start_year}0101 TO 30001231]"
    elif end_year and not start_year:
        date_filter = f" AND date_received:[00010101 TO {end_year}1231]"

    while True:
        search_query = f"device.device_report_product_code:{product_code}{date_filter}"
        params = {
            "search": search_query,
            "sort": "date_received:desc",
            "limit": limit,
            "skip": skip
        }

        resp = requests.get(base_url, params=params, timeout=60)

        if resp.status_code == 404:
            break

        if resp.status_code != 200:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text[:400]}")

        payload = resp.json()
        results = payload.get("results", [])
        if not results:
            break

        all_results.extend(results)

        if len(all_results) >= max_records:
            break

        skip += limit
        time.sleep(0.2)

        if skip > 25000:
            break

    if max_records != float("inf"):
        all_results = all_results[:max_records]

    return pd.json_normalize(all_results)


# ===============================
# Risk classification (event_type)
# ===============================
def classify_risk(event_type):
    if not isinstance(event_type, str):
        return "Low"
    et = event_type.strip().lower()
    if et in ["death", "serious injury"]:
        return "High"
    if et == "malfunction":
        return "Medium"
    return "Low"


# ===============================
# Streamlit UI
# ===============================
st.set_page_config(page_title="MAUDE Explorer (OpenFDA)", layout="wide")
st.title("MAUDE Explorer (OpenFDA)")
st.write("Filter by product code and years. View key analytics and export data (CSV/Excel).")

with st.sidebar:
    st.header("Filters")

    product_code = st.text_input("FDA Product Code").strip().upper()

    start_year = st.number_input(
        "Start year (YYYY)",
        min_value=1900,
        max_value=3000,
        value=2010,
        step=1
    )

    end_year = st.number_input(
        "End year (YYYY)",
        min_value=1900,
        max_value=3000,
        value=date.today().year,
        step=1
    )

    max_records = st.number_input(
        "Max records (0 = no limit)",
        min_value=0,
        value=2000,
        step=500
    )

run = st.button("Search")


if run:
    if not product_code:
        st.error("Please enter a product code.")
        st.stop()

    if start_year > end_year:
        st.error("Start year cannot be greater than end year.")
        st.stop()

    max_choice = None if max_records == 0 else int(max_records)

    with st.spinner("Fetching MAUDE data from OpenFDA..."):
        df = fetch_maude_reports(
            product_code=product_code,
            max_records=max_choice,
            start_year=str(int(start_year)),
            end_year=str(int(end_year))
        )

    st.success(f"Total records: {len(df):,}")

    if df.empty:
        st.info("No records found. Try expanding the year range or confirming the product code.")
        st.stop()

    # ===============================
    # Analytics
    # ===============================
    st.subheader("📊 Analytics")

    # ===============================
    # Analytics (no risk classification)
    # ===============================

    # Prepare date dataframe
    if "date_received" in df.columns:
        df["date_received"] = pd.to_datetime(df["date_received"], errors="coerce")
        df_dates = df.dropna(subset=["date_received"]).copy()
    else:
        df_dates = pd.DataFrame()


    # ---- Top Adverse Events ----
    if "event_type" in df.columns:
        st.write("### Top Adverse Events (event_type)")
        event_counts = df["event_type"].fillna("Unknown").value_counts()
        event_counts = event_counts.sort_values(ascending=False).head(10)
        st.bar_chart(event_counts)
    else:
        st.info("Column 'event_type' not found.")


    # ---- Yearly Trend ----
    if not df_dates.empty:
        st.write("### Yearly Event Trend")
        yearly = df_dates.groupby(df_dates["date_received"].dt.year).size().sort_index()
        st.bar_chart(yearly)

        # ---- Monthly Trend ----
        st.write("### Monthly Event Trend (last 24 months)")

        monthly = df_dates.set_index("date_received").resample("MS").size().sort_index()
        if len(monthly) > 24:
            monthly = monthly.iloc[-24:]

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(monthly.index, monthly.values, marker="o", linewidth=2)
        ax.set_xlabel("Month")
        ax.set_ylabel("Number of Reports")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    else:
        st.info("No valid dates found.")


    # ---- Top Device Problems ----
    if "product_problems" in df.columns:
        all_device_probs = []
        for probs in df["product_problems"].dropna():
            if isinstance(probs, list):
                all_device_probs.extend([p for p in probs if isinstance(p, str)])
            elif isinstance(probs, str) and probs.strip():
                all_device_probs.append(probs)

        if all_device_probs:
            device_counts = pd.Series(all_device_probs).value_counts().head(10)
            st.write("### Top Device Problems")
            st.bar_chart(device_counts)
        else:
            st.info("No device problems found.")


    # ---- Top Patient Problems ----
    patient_probs = []

    if "patient_problems" in df.columns:
        for x in df["patient_problems"].dropna():
            if isinstance(x, list):
                patient_probs.extend(x)
            elif isinstance(x, str):
                patient_probs.append(x)

    if "patient" in df.columns:
        for patient_list in df["patient"].dropna():
            if isinstance(patient_list, list):
                for p in patient_list:
                    if isinstance(p, dict) and "patient_problems" in p:
                        probs = p["patient_problems"]
                        if isinstance(probs, list):
                            patient_probs.extend(probs)
                        elif isinstance(probs, str):
                            patient_probs.append(probs)

    if patient_probs:
        patient_counts = pd.Series(patient_probs).value_counts().head(10)
        st.write("### Top Patient Problems")
        st.bar_chart(patient_counts)
    else:
        st.info("No patient problems found.")


    # ---- Top Manufacturers ----
    manu_names = []

    if "manufacturer_name" in df.columns:
        manu_names.extend(df["manufacturer_name"].dropna().tolist())

    if "device" in df.columns:
        for device_list in df["device"].dropna():
            if isinstance(device_list, list):
                for dev in device_list:
                    if isinstance(dev, dict) and "manufacturer_d_name" in dev:
                        manu_names.append(dev["manufacturer_d_name"])

    if manu_names:
        manu_counts = pd.Series(manu_names).value_counts().head(10)
        st.write("### Top Manufacturers")
        st.bar_chart(manu_counts)
    else:
        st.info("No manufacturers found.")

    # ===============================
    # Preview (smaller)
    # ===============================
    st.subheader("Preview (top 200 rows)")
    st.dataframe(df.head(200))

    # ===============================
    # Downloads
    # ===============================
    st.subheader("Download")

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download CSV",
        data=csv_bytes,
        file_name=f"maude_{product_code}_{int(start_year)}_{int(end_year)}.csv",
        mime="text/csv"
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="maude")

    st.download_button(
        label="Download Excel",
        data=buffer.getvalue(),
        file_name=f"maude_{product_code}_{int(start_year)}_{int(end_year)}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )