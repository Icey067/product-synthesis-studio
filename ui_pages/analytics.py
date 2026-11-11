import streamlit as st
import pandas as pd
from datetime import date
from services.analytics_service import AnalyticsService

def render_analytics():
    st.markdown("## 📈 Performance Tracking & Product Evolution")
    st.markdown("Record real-world sales metrics and customer feedback to guide continuous product iterations.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    with st.expander("➕ Log Commercial Performance Record", expanded=False):
        with st.form("log_metric_form"):
            col1, col2 = st.columns(2)
            with col1:
                log_date = st.date_input("Date", value=date.today())
                visits = st.number_input("Landing Page Visits", min_value=0, value=100, step=10)
                sales = st.number_input("Completed Sales", min_value=0, value=3, step=1)
            with col2:
                revenue = st.number_input("Net Revenue ($)", min_value=0.0, value=81.0, step=10.0)
                questions = st.number_input("Customer Support Questions", min_value=0, value=1, step=1)
                refunds = st.number_input("Refund Requests", min_value=0, value=0, step=1)

            notes = st.text_area("Customer Feedback / Common Friction Notes", placeholder="e.g., Customers loved Chapter 4 worksheet...")

            submit = st.form_submit_button("Save Performance Entry")
            if submit:
                AnalyticsService.record_metric(
                    project_id=project_id,
                    record_date=str(log_date),
                    visits=visits,
                    sales=sales,
                    revenue=revenue,
                    customer_questions=questions,
                    refunds=refunds,
                    feedback_notes=notes.strip(),
                )
                st.success("Entry logged successfully!")
                st.rerun()

    metrics = AnalyticsService.get_metrics(project_id)
    if not metrics:
        st.info("No analytics recorded for this project yet. Log your first sales entry above.")
        return

    df = pd.DataFrame(metrics)
    total_rev = df["revenue"].sum()
    total_sales = df["sales"].sum()
    total_visits = df["visits"].sum()
    conv_rate = (total_sales / total_visits * 100) if total_visits > 0 else 0.0

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Total Revenue", f"${total_rev:,.2f}")
    with k2:
        st.metric("Total Sales", f"{total_sales}")
    with k3:
        st.metric("Page Visits", f"{total_visits:,}")
    with k4:
        st.metric("Conversion Rate", f"{conv_rate:.2f}%")

    st.markdown("---")
    st.markdown("### 📊 Performance Trend")
    st.line_chart(df.set_index("record_date")[["sales", "visits"]])
    st.bar_chart(df.set_index("record_date")[["revenue"]])

    st.markdown("### 💬 Qualitative Customer Feedback Logs")
    for m in metrics:
        if m.get("feedback_notes"):
            st.markdown(f"• **{m['record_date']}:** {m['feedback_notes']}")

