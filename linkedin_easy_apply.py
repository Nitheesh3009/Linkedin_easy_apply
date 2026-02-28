"""
LinkedIn Easy Apply Automation Script
======================================
Uses Playwright to search for jobs and auto-submit Easy Apply applications.

Requirements:
    pip install playwright python-dotenv
    playwright install chromium

Setup:
    Create a .env file with:
        LINKEDIN_EMAIL=your@email.com
        LINKEDIN_PASSWORD=yourpassword
"""

import os
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

@dataclass
class JobSearchConfig:
    # Search filters
    keywords: str = "Software Engineer"
    location: str = "United States"
    remote_only: bool = True           # Filter to remote jobs
    easy_apply_only: bool = True       # Only Easy Apply jobs
    date_posted: str = "r86400"        # r86400=24h, r604800=1 week, r2592000=1 month

    # How many jobs to apply to per run
    max_applications: int = 10

    # Your answers to common Easy Apply questions
    phone_number: str = "5551234567"
    years_of_experience: str = "3"
    current_salary: str = "80000"
    desired_salary: str = "100000"
    cover_letter: str = (
        "I am excited to apply for this position. I believe my skills and "
        "experience make me a strong candidate, and I look forward to contributing "
        "to your team."
    )

    # Answers for yes/no questions (True = Yes)
    legally_authorized: bool = True
    requires_sponsorship: bool = False
    willing_to_relocate: bool = False

    # Output file to log applied jobs
    log_file: str = "applied_jobs.csv"


# ─── MAIN BOT CLASS ───────────────────────────────────────────────────────────

class LinkedInEasyApplyBot:
    def __init__(self, config: JobSearchConfig):
        self.config = config
        self.email = os.getenv("LINKEDIN_EMAIL")
        self.password = os.getenv("LINKEDIN_PASSWORD")
        self.applied_count = 0
        self.applied_jobs: list[dict] = []

        if not self.email or not self.password:
            raise ValueError("Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in your .env file.")

    def run(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)  # Set headless=True to run in background
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            self.page = context.new_page()

            try:
                self._login()
                self._search_jobs()
                self._process_job_listings()
            finally:
                self._save_log()
                browser.close()
                log.info(f"Done. Applied to {self.applied_count} jobs.")

    # ── Login ──────────────────────────────────────────────────────────────────

    def _login(self):
        log.info("Logging in to LinkedIn...")
        self.page.goto("https://www.linkedin.com/login")
        self.page.fill("#username", self.email)
        self.page.fill("#password", self.password)
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/feed/**", timeout=15000)
        log.info("Logged in successfully.")
        self._human_delay()

    # ── Search ─────────────────────────────────────────────────────────────────

    def _search_jobs(self):
        log.info(f"Searching for: {self.config.keywords} in {self.config.location}")

        params = {
            "keywords": self.config.keywords,
            "location": self.config.location,
            "f_TPR": self.config.date_posted,
        }
        if self.config.easy_apply_only:
            params["f_LF"] = "f_AL"   # Easy Apply filter
        if self.config.remote_only:
            params["f_WT"] = "2"       # Remote filter

        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"https://www.linkedin.com/jobs/search/?{query}"
        self.page.goto(url)
        self.page.wait_for_selector(".jobs-search-results-list", timeout=15000)
        self._human_delay()

    # ── Job listing loop ───────────────────────────────────────────────────────

    def _process_job_listings(self):
        while self.applied_count < self.config.max_applications:
            job_cards = self.page.query_selector_all(".job-card-container")
            if not job_cards:
                log.warning("No job cards found.")
                break

            for card in job_cards:
                if self.applied_count >= self.config.max_applications:
                    break

                try:
                    card.click()
                    self._human_delay(1, 2)

                    title = self._safe_text(".job-details-jobs-unified-top-card__job-title")
                    company = self._safe_text(".job-details-jobs-unified-top-card__company-name")
                    log.info(f"Viewing: {title} @ {company}")

                    # Check for Easy Apply button
                    easy_apply_btn = self.page.query_selector(".jobs-apply-button--top-card")
                    if not easy_apply_btn or "Easy Apply" not in easy_apply_btn.inner_text():
                        log.info("  → Skipping (no Easy Apply button)")
                        continue

                    easy_apply_btn.click()
                    self._human_delay()

                    success = self._complete_application()
                    if success:
                        self.applied_count += 1
                        self.applied_jobs.append({
                            "title": title,
                            "company": company,
                            "status": "Applied",
                        })
                        log.info(f"  ✓ Applied! ({self.applied_count}/{self.config.max_applications})")
                    else:
                        log.info("  → Skipped (complex application)")

                except Exception as e:
                    log.warning(f"  Error on job card: {e}")
                    self._close_modal()

            # Try to go to next page
            if not self._next_page():
                break

    # ── Application form handler ───────────────────────────────────────────────

    def _complete_application(self) -> bool:
        """
        Walks through Easy Apply modal pages, filling fields and submitting.
        Returns True if submitted, False if bailed out.
        """
        max_steps = 8  # Bail if more than this many pages (too complex)

        for step in range(max_steps):
            self._human_delay(1, 2)

            # Fill visible form fields
            self._fill_text_fields()
            self._fill_select_fields()
            self._answer_radio_buttons()

            # Check if we can submit
            submit_btn = self.page.query_selector("button[aria-label='Submit application']")
            if submit_btn:
                submit_btn.click()
                self._human_delay(2, 3)
                return True

            # Otherwise go to next step
            next_btn = self.page.query_selector("button[aria-label='Continue to next step']")
            review_btn = self.page.query_selector("button[aria-label='Review your application']")

            if review_btn:
                review_btn.click()
            elif next_btn:
                next_btn.click()
            else:
                log.info("  → No next/submit button found, bailing")
                self._close_modal()
                return False

        log.info("  → Too many steps, bailing")
        self._close_modal()
        return False

    def _fill_text_fields(self):
        """Fill common text input fields based on their labels."""
        inputs = self.page.query_selector_all("input[type='text'], input[type='tel'], textarea")
        for inp in inputs:
            try:
                label = self._get_field_label(inp)
                value = self._guess_answer(label)
                if value and not inp.get_attribute("value"):
                    inp.fill(value)
                    self._human_delay(0.2, 0.5)
            except Exception:
                pass

    def _fill_select_fields(self):
        """Fill dropdowns."""
        selects = self.page.query_selector_all("select")
        for sel in selects:
            try:
                options = sel.query_selector_all("option")
                if len(options) > 1:
                    # Pick second option if first is a placeholder
                    sel.select_option(index=1)
            except Exception:
                pass

    def _answer_radio_buttons(self):
        """Answer yes/no radio buttons."""
        fieldsets = self.page.query_selector_all("fieldset")
        for fieldset in fieldsets:
            try:
                legend = fieldset.query_selector("legend")
                label = legend.inner_text().lower() if legend else ""
                radios = fieldset.query_selector_all("input[type='radio']")
                if not radios:
                    continue

                if any(w in label for w in ["authorized", "eligible", "legally"]):
                    choice = "Yes" if self.config.legally_authorized else "No"
                elif any(w in label for w in ["sponsor", "visa"]):
                    choice = "Yes" if self.config.requires_sponsorship else "No"
                elif "relocat" in label:
                    choice = "Yes" if self.config.willing_to_relocate else "No"
                else:
                    choice = None

                if choice:
                    for radio in radios:
                        if radio.get_attribute("value", "") == choice or \
                           self.page.query_selector(f"label[for='{radio.get_attribute('id')}']") and \
                           choice.lower() in self.page.query_selector(
                               f"label[for='{radio.get_attribute('id')}']"
                           ).inner_text().lower():
                            radio.click()
                            break
            except Exception:
                pass

    def _guess_answer(self, label: str) -> Optional[str]:
        """Map field labels to answers from config."""
        label = label.lower()
        if any(w in label for w in ["phone", "mobile", "tel"]):
            return self.config.phone_number
        if any(w in label for w in ["year", "experience"]):
            return self.config.years_of_experience
        if "current salary" in label:
            return self.config.current_salary
        if any(w in label for w in ["desired", "expected", "salary"]):
            return self.config.desired_salary
        if "cover" in label:
            return self.config.cover_letter
        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_field_label(self, element) -> str:
        """Try to find the label associated with an input element."""
        try:
            el_id = element.get_attribute("id")
            if el_id:
                label = self.page.query_selector(f"label[for='{el_id}']")
                if label:
                    return label.inner_text()
        except Exception:
            pass
        return ""

    def _safe_text(self, selector: str) -> str:
        try:
            el = self.page.query_selector(selector)
            return el.inner_text().strip() if el else ""
        except Exception:
            return ""

    def _close_modal(self):
        try:
            dismiss = self.page.query_selector("button[aria-label='Dismiss']")
            if dismiss:
                dismiss.click()
                self._human_delay(0.5, 1)
                # Confirm discard if prompted
                discard = self.page.query_selector("button[data-control-name='discard_application_confirm_btn']")
                if discard:
                    discard.click()
        except Exception:
            pass

    def _next_page(self) -> bool:
        try:
            next_btn = self.page.query_selector("button[aria-label='View next page']")
            if next_btn:
                next_btn.click()
                self.page.wait_for_selector(".jobs-search-results-list", timeout=10000)
                self._human_delay(2, 3)
                return True
        except Exception:
            pass
        return False

    def _human_delay(self, min_sec: float = 1.0, max_sec: float = 2.5):
        """Sleep a random amount to mimic human behavior."""
        time.sleep(random.uniform(min_sec, max_sec))

    def _save_log(self):
        if not self.applied_jobs:
            return
        import csv
        file_exists = os.path.isfile(self.config.log_file)
        with open(self.config.log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["title", "company", "status"])
            if not file_exists:
                writer.writeheader()
            writer.writerows(self.applied_jobs)
        log.info(f"Log saved to {self.config.log_file}")


# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config = JobSearchConfig(
        keywords="Software Engineer",
        location="United States",
        remote_only=True,
        easy_apply_only=True,
        date_posted="r86400",       # Last 24 hours
        max_applications=10,

        # ── Fill in your details below ──
        phone_number="5551234567",
        years_of_experience="3",
        current_salary="80000",
        desired_salary="100000",
        cover_letter=(
            "I am excited to apply for this position and believe my experience "
            "aligns well with what you're looking for. I look forward to the "
            "opportunity to contribute to your team."
        ),
        legally_authorized=True,
        requires_sponsorship=False,
        willing_to_relocate=False,
    )

    bot = LinkedInEasyApplyBot(config)
    bot.run()