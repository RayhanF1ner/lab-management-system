"""
Computer Lab Management System (Streamlit + MySQL)

Setup
  1. mysql -u root -p < lab_schema.sql
  2. pip install streamlit mysql-connector-python pandas
  3. Set credentials via environment variables (defaults shown):
       LAB_DB_HOST=localhost  LAB_DB_USER=root  LAB_DB_PASSWORD=""  LAB_DB_NAME=computer_lab
  4. streamlit run lab_management.py
"""
import functools
import os
from datetime import date

import mysql.connector
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Computer Lab Management", page_icon="🖥️", layout="wide")

CATEGORIES = ["Laptop", "Desktop", "Monitor", "Keyboard/Mouse", "Networking",
              "Printer/Scanner", "Projector", "Cable/Adapter", "Storage", "Other"]
ROLES = ["Student", "Faculty", "Staff"]


# ---------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------
@st.cache_resource
def _connection():
    return mysql.connector.connect(
        host=os.getenv("LAB_DB_HOST", "localhost"),
        user=os.getenv("LAB_DB_USER", "root"),
        password=os.getenv("LAB_DB_PASSWORD", ""),
        database=os.getenv("LAB_DB_NAME", "computer_lab"),
    )


def get_conn():
    conn = _connection()
    conn.ping(reconnect=True, attempts=3, delay=1)  # survive dropped connections
    return conn


def query(sql, params=()):
    """Run a SELECT and return a DataFrame."""
    cur = get_conn().cursor()
    try:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=cur.column_names)
    finally:
        cur.close()


def scalar(sql, params=()):
    df = query(sql, params)
    value = df.iloc[0, 0] if not df.empty else 0
    return 0 if pd.isna(value) else int(value)


def run(statements):
    """Run one or more (sql, params) writes in a single transaction."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        for sql, params in statements:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def call_proc(name, args):
    """Call a stored procedure, commit, and return its (possibly OUT) args."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        result = cur.callproc(name, args)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def db_page(fn):
    """Show database errors nicely instead of crashing the page."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except mysql.connector.Error as e:
            st.error(f"Database error: {e.msg}")
    return wrapper


def choices(df, id_col, name_col):
    """Build {'3 · Lab Name': 3} for use in selectboxes."""
    return {f"{r[id_col]} · {r[name_col]}": r[id_col] for _, r in df.iterrows()}


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
@db_page
def dashboard():
    st.title("🖥️ Lab Dashboard")

    total = scalar("SELECT SUM(Total_Quantity) FROM asset_stock")
    available = scalar("SELECT SUM(Number_Available) FROM asset_stock")
    out = scalar("SELECT COUNT(*) FROM checkout WHERE Return_Date IS NULL")
    overdue = scalar("SELECT COUNT(*) FROM checkout WHERE Return_Date IS NULL AND Due_Date < CURDATE()")

    st.subheader("Equipment")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total units", total)
    c2.metric("Available", available)
    c3.metric("Checked out", out)
    c4.metric("Overdue", overdue)

    st.subheader("Workstations")
    ws = query("SELECT Status, COUNT(*) AS n FROM workstation GROUP BY Status")
    counts = dict(zip(ws["Status"], ws["n"])) if not ws.empty else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Available", int(counts.get("Available", 0)))
    c2.metric("In use", int(counts.get("In Use", 0)))
    c3.metric("Maintenance", int(counts.get("Maintenance", 0)))
    c4.metric("Open tickets", scalar("SELECT COUNT(*) FROM maintenance_ticket WHERE Status = 'Open'"))


@db_page
def add_lab():
    st.title("Add New Lab")
    with st.form("lab_form", clear_on_submit=True):
        name = st.text_input("Lab name")
        building = st.text_input("Building")
        room = st.text_input("Room number")
        if st.form_submit_button("Add Lab"):
            if not name.strip():
                st.warning("Lab name is required.")
            else:
                run([("INSERT INTO lab (Name, Building, Room_No) VALUES (%s, %s, %s)",
                      (name.strip(), building, room))])
                st.success("Lab added successfully!")


@db_page
def add_workstation():
    st.title("Add Workstation")
    labs = choices(query("SELECT Lab_ID, Name FROM lab"), "Lab_ID", "Name")
    if not labs:
        st.warning("Add a lab first.")
        return
    with st.form("ws_form", clear_on_submit=True):
        lab = st.selectbox("Lab", list(labs))
        name = st.text_input("Workstation name (e.g. PC-04)")
        specs = st.text_input("Specs (CPU / RAM / Storage)")
        if st.form_submit_button("Add Workstation"):
            if not name.strip():
                st.warning("Workstation name is required.")
            else:
                run([("INSERT INTO workstation (Lab_ID, Name, Specs) VALUES (%s, %s, %s)",
                      (int(labs[lab]), name.strip(), specs))])
                st.success("Workstation added successfully!")


@db_page
def add_equipment():
    st.title("Add Equipment to Lab")
    vendors = choices(query("SELECT Vendor_ID, Name FROM vendor"), "Vendor_ID", "Name")
    labs = choices(query("SELECT Lab_ID, Name FROM lab"), "Lab_ID", "Name")
    if not vendors or not labs:
        st.warning("Please add at least one Vendor and one Lab first.")
        return

    with st.form("equip_form", clear_on_submit=True):
        tag = st.text_input("Asset Tag / Serial No.")
        name = st.text_input("Equipment name")
        category = st.selectbox("Category", CATEGORIES)
        brand = st.text_input("Brand")
        model = st.text_input("Model")
        specs = st.text_input("Specifications")
        vendor = st.selectbox("Vendor", list(vendors))
        lab = st.selectbox("Lab", list(labs))
        qty = st.number_input("Number of units", min_value=1, value=1, step=1)

        if st.form_submit_button("Add Equipment"):
            if not tag.strip() or not name.strip():
                st.warning("Asset tag and name are required.")
            else:
                call_proc("AddNewAsset", (tag.strip(), name.strip(), category, brand, model, specs,
                                          int(vendors[vendor]), int(labs[lab]), int(qty)))
                st.success("Equipment added successfully!")


@db_page
def lend_equipment():
    st.title("Lend Equipment")
    users = choices(query("SELECT User_ID, Name FROM lab_user"), "User_ID", "Name")
    assets = query("""SELECT a.Asset_Tag, a.Asset_Name, s.Number_Available
                      FROM asset a JOIN asset_stock s ON a.Asset_Tag = s.Asset_Tag
                      WHERE s.Number_Available > 0""")
    if not users:
        st.warning("No users registered yet.")
        return
    if assets.empty:
        st.warning("No equipment is currently available.")
        return

    labels = {f"{r.Asset_Tag} · {r.Asset_Name} ({r.Number_Available} available)": r.Asset_Tag
              for r in assets.itertuples()}
    with st.form("lend_form"):
        asset = st.selectbox("Equipment", list(labels))
        user = st.selectbox("Borrower", list(users))
        days = st.number_input("Loan period (days)", min_value=1, max_value=90, value=7)
        if st.form_submit_button("Lend Equipment"):
            call_proc("IssueAsset", (labels[asset], int(users[user]), int(days)))
            st.success("Equipment lent successfully!")


@db_page
def return_equipment():
    st.title("Return Equipment")
    open_loans = query("""
        SELECT c.Checkout_ID, a.Asset_Name, c.Asset_Tag, u.Name AS Borrower, c.Due_Date
        FROM checkout c
        JOIN asset a ON a.Asset_Tag = c.Asset_Tag
        JOIN lab_user u ON u.User_ID = c.User_ID
        WHERE c.Return_Date IS NULL
        ORDER BY c.Due_Date""")
    if open_loans.empty:
        st.info("Nothing is currently checked out.")
        return

    labels = {f"#{r.Checkout_ID} · {r.Asset_Name} ({r.Asset_Tag}) - {r.Borrower}, due {r.Due_Date}": r.Checkout_ID
              for r in open_loans.itertuples()}
    with st.form("return_form"):
        loan = st.selectbox("Checked-out item", list(labels))
        return_date = st.date_input("Return date", value=date.today())
        condition = st.text_input("Condition notes (optional)", placeholder="e.g. Good / cracked screen")
        if st.form_submit_button("Return Equipment"):
            result = call_proc("ReturnAsset", (int(labels[loan]), return_date, condition, 0))
            fine = float(result[3] or 0)
            if fine > 0:
                st.success(f"Equipment returned. Late fine: {fine:.2f}")
            else:
                st.success("Equipment returned successfully!")


@db_page
def search_equipment():
    st.title("Search Equipment")
    fields = {"Asset Tag": "a.Asset_Tag", "Name": "a.Asset_Name",
              "Brand": "a.Brand", "Category": "a.Category"}
    field = st.selectbox("Search by", list(fields))
    text = st.text_input(f"Enter {field.lower()}")

    if st.button("🔍 Search"):
        df = query(f"""
            SELECT a.Asset_Tag, a.Asset_Name AS Name, a.Category, a.Brand, a.Model, a.Specs,
                   v.Name AS Vendor, l.Name AS Lab,
                   s.Number_Available AS Available, s.Total_Quantity AS Total
            FROM asset a
            JOIN asset_stock s ON s.Asset_Tag = a.Asset_Tag
            LEFT JOIN vendor v ON v.Vendor_ID = a.Vendor_ID
            LEFT JOIN lab l ON l.Lab_ID = a.Lab_ID
            WHERE {fields[field]} LIKE %s
            ORDER BY a.Asset_Name""", (f"%{text}%",))
        if df.empty:
            st.warning("No equipment found matching the search criteria.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)


@db_page
def add_vendor():
    st.title("Add New Vendor")
    with st.form("vendor_form", clear_on_submit=True):
        name = st.text_input("Vendor name")
        email = st.text_input("Contact email")
        phone = st.text_input("Phone")
        city = st.text_input("City")
        if st.form_submit_button("Add Vendor"):
            if not name.strip():
                st.warning("Vendor name is required.")
            else:
                run([("INSERT INTO vendor (Name, Contact_Email, Phone, City) VALUES (%s, %s, %s, %s)",
                      (name.strip(), email, phone, city))])
                st.success("Vendor added successfully!")


@db_page
def add_user():
    st.title("Add New Lab User")
    with st.form("user_form", clear_on_submit=True):
        name = st.text_input("Name")
        email = st.text_input("Email")
        role = st.selectbox("Role", ROLES)
        dept = st.text_input("Department")
        if st.form_submit_button("Add User"):
            if not name.strip():
                st.warning("Name is required.")
            else:
                run([("INSERT INTO lab_user (Name, Email, Role, Department) VALUES (%s, %s, %s, %s)",
                      (name.strip(), email, role, dept))])
                st.success("User added successfully!")


@db_page
def delete_equipment():
    st.title("Delete Equipment")
    assets = query("SELECT Asset_Tag, Asset_Name FROM asset ORDER BY Asset_Name")
    if assets.empty:
        st.info("No equipment to delete.")
        return
    labels = {f"{r.Asset_Tag} · {r.Asset_Name}": r.Asset_Tag for r in assets.itertuples()}
    choice = st.selectbox("Equipment", list(labels))
    confirm = st.checkbox("I understand this also deletes its checkout history")
    if st.button("Delete Equipment", disabled=not confirm):
        call_proc("DeleteAsset", (labels[choice],))
        st.success("Equipment deleted successfully!")


@db_page
def checked_out_equipment():
    st.title("Checked-Out Equipment (not yet returned)")
    df = query("""
        SELECT c.Checkout_ID, a.Asset_Tag, a.Asset_Name AS Equipment, u.Name AS Borrower,
               u.Role, c.Issue_Date, c.Due_Date,
               GREATEST(DATEDIFF(CURDATE(), c.Due_Date), 0) AS Days_Overdue
        FROM checkout c
        JOIN asset a ON a.Asset_Tag = c.Asset_Tag
        JOIN lab_user u ON u.User_ID = c.User_ID
        WHERE c.Return_Date IS NULL
        ORDER BY c.Due_Date""")
    if df.empty:
        st.info("No equipment is currently checked out.")
        return
    overdue = int((df["Days_Overdue"] > 0).sum())
    if overdue:
        st.error(f"{overdue} item(s) overdue")
    st.dataframe(df, use_container_width=True, hide_index=True)


@db_page
def workstations():
    st.title("Workstations & Sessions")
    df = query("""
        SELECT w.Workstation_ID, l.Name AS Lab, w.Name, w.Specs, w.Status,
               u.Name AS Current_User, s.Start_Time
        FROM workstation w
        JOIN lab l ON l.Lab_ID = w.Lab_ID
        LEFT JOIN session_log s ON s.Workstation_ID = w.Workstation_ID AND s.End_Time IS NULL
        LEFT JOIN lab_user u ON u.User_ID = s.User_ID
        ORDER BY l.Name, w.Name""")
    if df.empty:
        st.warning("No workstations registered yet.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)

    start_tab, end_tab, history_tab = st.tabs(["Start session", "End session", "Recent sessions"])

    with start_tab:
        free = df[df["Status"] == "Available"]
        users = choices(query("SELECT User_ID, Name FROM lab_user"), "User_ID", "Name")
        if free.empty:
            st.info("No workstations are free right now.")
        elif not users:
            st.info("No users registered yet.")
        else:
            ws_map = {f"{r.Lab} · {r.Name}": r.Workstation_ID for r in free.itertuples()}
            with st.form("start_session"):
                ws = st.selectbox("Workstation", list(ws_map))
                user = st.selectbox("User", list(users))
                if st.form_submit_button("Start Session"):
                    call_proc("StartSession", (int(ws_map[ws]), int(users[user])))
                    st.success("Session started!")
                    st.rerun()

    with end_tab:
        busy = df[df["Status"] == "In Use"]
        if busy.empty:
            st.info("No active sessions.")
        else:
            ws_map = {f"{r.Lab} · {r.Name} ({r.Current_User})": r.Workstation_ID for r in busy.itertuples()}
            with st.form("end_session"):
                ws = st.selectbox("Active session", list(ws_map))
                if st.form_submit_button("End Session"):
                    call_proc("EndSession", (int(ws_map[ws]),))
                    st.success("Session ended!")
                    st.rerun()

    with history_tab:
        hist = query("""
            SELECT s.Session_ID, w.Name AS Workstation, u.Name AS User, s.Start_Time, s.End_Time,
                   TIMESTAMPDIFF(MINUTE, s.Start_Time, COALESCE(s.End_Time, NOW())) AS Minutes
            FROM session_log s
            JOIN workstation w ON w.Workstation_ID = s.Workstation_ID
            JOIN lab_user u ON u.User_ID = s.User_ID
            ORDER BY s.Start_Time DESC LIMIT 50""")
        st.dataframe(hist, use_container_width=True, hide_index=True)


@db_page
def maintenance():
    st.title("Maintenance Tickets")
    report_tab, open_tab = st.tabs(["Report issue", "Open tickets"])

    with report_tab:
        ws_df = query("SELECT w.Workstation_ID, CONCAT(l.Name, ' · ', w.Name) AS Label "
                      "FROM workstation w JOIN lab l ON l.Lab_ID = w.Lab_ID ORDER BY Label")
        ws_map = {"(General / not workstation-specific)": None}
        ws_map.update({r.Label: int(r.Workstation_ID) for r in ws_df.itertuples()})
        users = {"(Anonymous)": None}
        users.update({k: int(v) for k, v in
                      choices(query("SELECT User_ID, Name FROM lab_user"), "User_ID", "Name").items()})

        with st.form("ticket_form", clear_on_submit=True):
            ws = st.selectbox("Workstation", list(ws_map))
            reporter = st.selectbox("Reported by", list(users))
            issue = st.text_area("Describe the issue")
            take_offline = st.checkbox("Take this workstation out of service")
            if st.form_submit_button("Submit Ticket"):
                if not issue.strip():
                    st.warning("Please describe the issue.")
                else:
                    stmts = [("INSERT INTO maintenance_ticket (Workstation_ID, Reported_By, Issue) "
                              "VALUES (%s, %s, %s)", (ws_map[ws], users[reporter], issue.strip()))]
                    if take_offline and ws_map[ws] is not None:
                        stmts.append(("UPDATE workstation SET Status = 'Maintenance' "
                                      "WHERE Workstation_ID = %s AND Status = 'Available'", (ws_map[ws],)))
                    run(stmts)
                    st.success("Ticket submitted.")

    with open_tab:
        tickets = query("""
            SELECT t.Ticket_ID, COALESCE(w.Name, 'General') AS Workstation, t.Issue,
                   COALESCE(u.Name, '-') AS Reported_By, t.Reported_At
            FROM maintenance_ticket t
            LEFT JOIN workstation w ON w.Workstation_ID = t.Workstation_ID
            LEFT JOIN lab_user u ON u.User_ID = t.Reported_By
            WHERE t.Status = 'Open' ORDER BY t.Reported_At""")
        if tickets.empty:
            st.info("No open tickets 🎉")
            return
        st.dataframe(tickets, use_container_width=True, hide_index=True)

        labels = {f"#{r.Ticket_ID} · {r.Workstation}: {r.Issue[:40]}": int(r.Ticket_ID)
                  for r in tickets.itertuples()}
        with st.form("resolve_form"):
            pick = st.selectbox("Ticket to resolve", list(labels))
            if st.form_submit_button("Mark Resolved"):
                tid = labels[pick]
                run([
                    ("UPDATE maintenance_ticket SET Status = 'Resolved', Resolved_At = NOW() "
                     "WHERE Ticket_ID = %s", (tid,)),
                    # Put the workstation back in service if no other tickets remain open
                    ("""UPDATE workstation w
                        JOIN maintenance_ticket t ON t.Workstation_ID = w.Workstation_ID
                        SET w.Status = 'Available'
                        WHERE t.Ticket_ID = %s AND w.Status = 'Maintenance'
                          AND NOT EXISTS (SELECT 1 FROM maintenance_ticket o
                                          WHERE o.Workstation_ID = w.Workstation_ID
                                            AND o.Status = 'Open')""", (tid,)),
                ])
                st.success("Ticket resolved.")
                st.rerun()


@db_page
def lab_staff():
    st.title("Lab Staff")
    df = query("""SELECT s.Staff_ID, s.Name, s.Role, s.Contact, l.Name AS Lab
                  FROM staff s LEFT JOIN lab l ON l.Lab_ID = s.Lab_ID""")
    if df.empty:
        st.warning("No lab staff found.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# App
# ---------------------------------------------------------------------
PAGES = {
    "Dashboard": dashboard,
    "Add Equipment": add_equipment,
    "Lend Equipment": lend_equipment,
    "Return Equipment": return_equipment,
    "Search Equipment": search_equipment,
    "Checked-Out Equipment": checked_out_equipment,
    "Delete Equipment": delete_equipment,
    "Workstations & Sessions": workstations,
    "Maintenance Tickets": maintenance,
    "Add New User": add_user,
    "Add New Vendor": add_vendor,
    "Add New Lab": add_lab,
    "Add Workstation": add_workstation,
    "Lab Staff": lab_staff,
}


def main():
    st.sidebar.title("🖥️ Computer Lab")
    choice = st.sidebar.selectbox("Menu", list(PAGES))
    PAGES[choice]()


if __name__ == "__main__":
    main()