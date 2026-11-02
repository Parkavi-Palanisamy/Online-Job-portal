import uuid
import os
from flask import request
from flask import send_from_directory
from flask import Flask, render_template, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from db import get_db_connection
from pypdf import PdfReader
app = Flask(__name__)
app.secret_key = "change-this-to-a-random-secret-key"

UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")
ALLOWED_EXTENSIONS = {"pdf"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
MAX_CONTENT_LENGTH = 5 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

@app.errorhandler(413)
def request_entity_too_large(error):
    flash("Resume file must be smaller than 5 MB.")
    return redirect(request.referrer or url_for("jobs"))

# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("index.html")


# ---------------- REGISTER ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]

        if not name or not email or not password:
            flash("Please fill in all required fields.")
            return redirect(url_for("register"))

        # Password length validation
        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("register"))

        if role not in ["job_seeker", "recruiter"]:
            flash("Please select a valid role.")
            return redirect(url_for("register"))

        connection = get_db_connection()
        cursor = connection.cursor()

        try:
            cursor.execute(
                """INSERT INTO users (name, email, password, role)
                   VALUES (%s, %s, %s, %s)""",
                (name, email, generate_password_hash(password), role)
            )
            connection.commit()
            flash("Registration successful! Please login.")
            return redirect(url_for("login"))

        except Exception as error:
            connection.rollback()

            if getattr(error, "errno", None) == 1062:
                flash("This email is already registered.")
            else:
                app.logger.exception("Registration failed")
                flash("Registration failed. Check your database settings.")

        finally:
            cursor.close()
            connection.close()

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM users WHERE email = %s",
                (email,)
            )
            user = cursor.fetchone()

            if user and check_password_hash(user["password"], password):
                session["user_id"] = user["user_id"]
                session["name"] = user["name"]
                session["role"] = user["role"]

                if user["role"] == "recruiter":
                    return redirect(url_for("recruiter_dashboard"))

                return redirect(url_for("dashboard"))

            flash("Invalid email or password.")

        finally:
            cursor.close()
            connection.close()

    return render_template("login.html")


# ---------------- JOB SEEKER DASHBOARD ----------------

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "job_seeker":
        return redirect(url_for("recruiter_dashboard"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT name, email, skills, role
            FROM users
            WHERE user_id = %s
        """, (session["user_id"],))

        user = cursor.fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        cursor.execute("""
            SELECT COUNT(*) AS total_applications
            FROM applications
            WHERE user_id = %s
        """, (session["user_id"],))

        application_data = cursor.fetchone()
        total_applications = application_data["total_applications"]

        return render_template(
            "dashboard.html",
            user=user,
            total_applications=total_applications
        )

    finally:
        cursor.close()
        connection.close()

# ---------------- RECRUITER DASHBOARD ----------------

@app.route("/recruiter-dashboard")
def recruiter_dashboard():
    print("DEBUG USER ID:", session.get("user_id"))
    print("DEBUG ROLE:", session.get("role"))
    
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "recruiter":
        flash("Access denied.")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        recruiter_id = session["user_id"]

        # Total jobs posted by this recruiter
        cursor.execute("""
            SELECT COUNT(*) AS total_jobs
            FROM jobs j
            JOIN companies c ON j.company_id = c.company_id
            WHERE c.recruiter_id = %s
        """, (recruiter_id,))

        total_jobs = cursor.fetchone()["total_jobs"]

        # Total applications received by this recruiter's jobs
        cursor.execute("""
            SELECT
                COUNT(a.application_id) AS total_applicants,
                COALESCE(SUM(
                    CASE WHEN a.status = 'Pending'
                    THEN 1 ELSE 0 END
                ), 0) AS pending_applications,
                COALESCE(SUM(
                    CASE WHEN a.status = 'Shortlisted'
                    THEN 1 ELSE 0 END
                ), 0) AS shortlisted_applications
            FROM applications a
            JOIN jobs j ON a.job_id = j.job_id
            JOIN companies c ON j.company_id = c.company_id
            WHERE c.recruiter_id = %s
        """, (recruiter_id,))

        stats = cursor.fetchone()

        return render_template(
            "recruiter_dashboard.html",
            total_jobs=total_jobs,
            total_applicants=stats["total_applicants"],
            pending_applications=stats["pending_applications"],
            shortlisted_applications=stats["shortlisted_applications"]
        )

    finally:
        cursor.close()
        connection.close()

# ---------------- POST A JOB ----------------
@app.route("/post-job", methods=["GET", "POST"])
def post_job():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "recruiter":
        flash("Only recruiters can post jobs.")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT company_id FROM companies WHERE recruiter_id = %s",
            (session["user_id"],)
        )
        company = cursor.fetchone()

        if not company:
            flash("Your recruiter account is not linked to a company.")
            return redirect(url_for("recruiter_dashboard"))

        if request.method == "POST":
            title = request.form["title"].strip()
            description = request.form["description"].strip()
            requirements = request.form["requirements"].strip()
            location = request.form["location"].strip()
            salary = request.form["salary"].strip()
            job_type = request.form["job_type"]

            if not title or not description or not location:
                flash("Please fill in all required fields.")
                return render_template("post_job.html")

            if job_type not in ["Full-Time", "Part-Time", "Internship"]:
                flash("Please select a valid job type.")
                return render_template("post_job.html")

            cursor.execute("""
                INSERT INTO jobs
                (company_id, title, description, requirements,
                 location, salary, job_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                company["company_id"],
                title,
                description,
                requirements,
                location,
                salary or None,
                job_type
            ))

            connection.commit()
            flash("Job posted successfully!")
            return redirect(url_for("my_posted_jobs"))

    except Exception:
        connection.rollback()
        app.logger.exception("Post job failed")
        flash("Could not post the job. Please check the job details.")

    finally:
        cursor.close()
        connection.close()

    return render_template("post_job.html")


# ---------------- MY POSTED JOBS ----------------
@app.route("/my-posted-jobs")
def my_posted_jobs():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "recruiter":
        flash("Only recruiters can view posted jobs.")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT j.job_id, j.title, j.description, j.location,
                   j.salary, j.job_type, j.posted_at, c.company_name
            FROM jobs j
            JOIN companies c ON j.company_id = c.company_id
            WHERE c.recruiter_id = %s
            ORDER BY j.posted_at DESC
        """, (session["user_id"],))

        jobs_list = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    return render_template("my_posted_jobs.html", jobs=jobs_list)


# ---------------- VIEW APPLICANTS ----------------
# IMPORTANT: This route must appear only ONCE.
@app.route("/job/<int:job_id>/applicants")
def view_applicants(job_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "recruiter":
        flash("Only recruiters can view applicants.")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Ensure this job belongs to the logged-in recruiter.
        cursor.execute("""
            SELECT j.job_id, j.title
            FROM jobs j
            JOIN companies c ON j.company_id = c.company_id
            WHERE j.job_id = %s
              AND c.recruiter_id = %s
        """, (job_id, session["user_id"]))

        job = cursor.fetchone()

        if not job:
            flash("Job not found or access denied.")
            return redirect(url_for("my_posted_jobs"))

        cursor.execute("""
            SELECT a.application_id, a.status, a.applied_at,
                   u.name AS applicant_name,
                   u.email AS applicant_email
            FROM applications a
            JOIN users u ON a.user_id = u.user_id
            WHERE a.job_id = %s
            ORDER BY a.applied_at DESC
        """, (job_id,))

        applicants = cursor.fetchall()

        return render_template(
            "applicants.html",
            job=job,
            applicants=applicants
        )

    finally:
        cursor.close()
        connection.close()

@app.route("/download_resume/<int:application_id>")
def download_resume(application_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "recruiter":
        flash("Only recruiters can download resumes.")
        return redirect(url_for("jobs"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT a.resume_path
            FROM applications a
            JOIN jobs j ON a.job_id = j.job_id
            JOIN companies c ON j.company_id = c.company_id
            WHERE a.application_id = %s
              AND c.recruiter_id = %s
        """, (application_id, session["user_id"]))

        application = cursor.fetchone()

        if not application or not application["resume_path"]:
            flash("Resume not found or access denied.")
            return redirect(url_for("recruiter_dashboard"))

        filename = os.path.basename(application["resume_path"])
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

        if not os.path.isfile(file_path):
            flash("Resume file is missing.")
            return redirect(url_for("recruiter_dashboard"))

        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            filename,
            as_attachment=True
        )

    except Exception:
        app.logger.exception("Resume download failed")
        flash("Unable to download resume.")
        return redirect(url_for("recruiter_dashboard"))

    finally:
        cursor.close()
        connection.close()

# ---------------- UPDATE APPLICATION STATUS ----------------
@app.route(
    "/application/<int:application_id>/status",
    methods=["POST"]
)
def update_application_status(application_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "recruiter":
        flash("Only recruiters can update application status.")
        return redirect(url_for("dashboard"))

    new_status = request.form.get("status")

    allowed_statuses = [
        "Pending", "Reviewed", "Shortlisted", "Rejected"
    ]

    if new_status not in allowed_statuses:
        flash("Invalid application status.")
        return redirect(url_for("my_posted_jobs"))

    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            UPDATE applications a
            JOIN jobs j ON a.job_id = j.job_id
            JOIN companies c ON j.company_id = c.company_id
            SET a.status = %s
            WHERE a.application_id = %s
              AND c.recruiter_id = %s
        """, (
            new_status,
            application_id,
            session["user_id"]
        ))

        connection.commit()

        if cursor.rowcount > 0:
            flash("Application status updated successfully!")
        else:
            flash("Application not found or access denied.")

    except Exception:
        connection.rollback()
        app.logger.exception("Application status update failed")
        flash("Could not update status. Check your database status column.")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("my_posted_jobs"))


# ---------------- BROWSE JOBS ----------------
@app.route("/jobs")
def jobs():
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT j.job_id, j.title, j.description, j.requirements,
                   j.location, j.salary, j.job_type, j.posted_at,
                   c.company_name,
                   c.location AS company_location
            FROM jobs j
            LEFT JOIN companies c ON j.company_id = c.company_id
            ORDER BY j.posted_at DESC
        """)

        job_list = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    return render_template("jobs.html", jobs=job_list)


# ---------------- APPLY FOR A JOB ----------------

@app.route("/apply/<int:job_id>", methods=["POST"])
def apply_job(job_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "job_seeker":
        flash("Only job seekers can apply for jobs.")
        return redirect(url_for("jobs"))

    resume = request.files.get("resume")

    if not resume or not resume.filename:
        flash("Please upload your PDF resume.")
        return redirect(url_for("jobs"))

    original_name = secure_filename(resume.filename)

    # 1. Check file extension
    if not original_name.lower().endswith(".pdf"):
        flash("Only PDF resumes are allowed.")
        return redirect(url_for("jobs"))

    # 2. Validate that the file is a readable PDF
    try:
        resume.stream.seek(0)
        pdf = PdfReader(resume.stream, strict=True)

        if pdf.is_encrypted:
            flash("Password-protected PDFs are not allowed.")
            return redirect(url_for("jobs"))

        if len(pdf.pages) == 0:
            flash("The PDF resume contains no pages.")
            return redirect(url_for("jobs"))

        # Ensure the PDF pages can be read
        for page in pdf.pages:
            _ = page.mediabox

        resume.stream.seek(0)

    except Exception:
        flash("Invalid or corrupted PDF. Please upload a valid PDF resume.")
        return redirect(url_for("jobs"))

    # 3. Generate a unique filename
    resume_filename = f"{uuid.uuid4().hex}.pdf"
    resume_path = os.path.join(
        app.config["UPLOAD_FOLDER"], resume_filename
    )

    connection = None
    cursor = None
    file_saved = False

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Check whether the job exists
        cursor.execute(
            "SELECT job_id FROM jobs WHERE job_id = %s",
            (job_id,)
        )

        if cursor.fetchone() is None:
            flash("Job not found.")
            return redirect(url_for("jobs"))

        # Prevent duplicate applications
        cursor.execute("""
            SELECT application_id
            FROM applications
            WHERE job_id = %s AND user_id = %s
        """, (job_id, session["user_id"]))

        if cursor.fetchone():
            flash("You have already applied for this job.")
            return redirect(url_for("jobs"))

        # 4. Save the validated PDF
        resume.save(resume_path)
        file_saved = True

        # 5. Store application in database
        cursor.execute("""
            INSERT INTO applications
                (job_id, user_id, resume_path)
            VALUES (%s, %s, %s)
        """, (
            job_id,
            session["user_id"],
            resume_filename
        ))

        connection.commit()
        flash("Application submitted successfully with your resume!")

    except Exception:
        if connection:
            connection.rollback()

        if file_saved and os.path.exists(resume_path):
            os.remove(resume_path)

        app.logger.exception("Job application failed")
        flash("Application failed. Please try again.")

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    return redirect(url_for("jobs"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            skills = request.form.get("skills", "").strip()

            if not name or not email:
                flash("Name and email are required.")
            else:
                cursor.execute("""
                    SELECT user_id FROM users
                    WHERE email = %s AND user_id != %s
                """, (email, user_id))

                if cursor.fetchone():
                    flash("This email is already registered.")
                else:
                    cursor.execute("""
                        UPDATE users
                        SET name = %s, email = %s, skills = %s
                        WHERE user_id = %s
                    """, (name, email, skills, user_id))

                    connection.commit()
                    flash("Profile updated successfully!")

        cursor.execute("""
            SELECT user_id, name, email, skills, role
            FROM users
            WHERE user_id = %s
        """, (user_id,))

        user = cursor.fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        return render_template("profile.html", user=user)

    except Exception:
        connection.rollback()
        app.logger.exception("Profile operation failed")
        flash("Unable to update profile. Please try again.")
        return redirect(url_for("profile"))

    finally:
        cursor.close()
        connection.close()
        
# ---------------- MY APPLICATIONS ----------------
@app.route("/my-applications")
def my_applications():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "job_seeker":
        flash("This page is for job seekers only.")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT a.application_id, a.status, a.applied_at,
                   j.title AS job_title,
                   j.location AS job_location,
                   j.salary, j.job_type,
                   c.company_name
            FROM applications a
            JOIN jobs j ON a.job_id = j.job_id
            LEFT JOIN companies c ON j.company_id = c.company_id
            WHERE a.user_id = %s
            ORDER BY a.applied_at DESC
        """, (session["user_id"],))

        applications = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "my_applications.html",
        applications=applications
    )


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
