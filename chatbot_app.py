# sternfield_timetable_bot.py
import streamlit as st
import json
from datetime import datetime, timedelta
import time as py_time
from threading import Thread, Event
import re

# Try importing plyer (desktop notifications). If not available, we'll ignore notifications.
try:
    from plyer import notification
    PLYER_AVAILABLE = True
except Exception:
    PLYER_AVAILABLE = False

# --- Configuration & Data Loading ---
TIMETABLE_FILE = "timetable_data.json"
TEACHER_ASSIGNMENTS_FILE = "teacher_assignments.json"
NOTIFICATION_WINDOW_MINUTES = 5  # Notify X minutes before class starts

def load_data(file_name):
    """
    Loads JSON data from a local file.
    Returns:
      - For timetable file: a list []
      - For teacher assignments file: a dict {}
    Handles missing file and invalid JSON gracefully.
    """
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return [] if file_name == TIMETABLE_FILE else {}
            return json.loads(content)
    except FileNotFoundError:
        return [] if file_name == TIMETABLE_FILE else {}
    except json.JSONDecodeError:
        st.error(f"Error: {file_name} contains invalid JSON.")
        return [] if file_name == TIMETABLE_FILE else {}

def save_assignments(assignments):
    """Saves teacher assignments to a local file (pretty-printed)."""
    try:
        with open(TEACHER_ASSIGNMENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(assignments, f, indent=2)
    except Exception as e:
        st.error(f"Failed to save assignments: {e}")

# Load initial data (module level)
TIMETABLE = load_data(TIMETABLE_FILE)

# Ensure session_state defaults exist before UI code runs
if "assignments" not in st.session_state:
    st.session_state.assignments = load_data(TEACHER_ASSIGNMENTS_FILE) or {}
if "checker_thread" not in st.session_state:
    st.session_state.checker_thread = None
if "checker_stop_event" not in st.session_state:
    st.session_state.checker_stop_event = None
if "last_checked_teacher" not in st.session_state:
    st.session_state.last_checked_teacher = None
if "reg_teacher_name" not in st.session_state:
    st.session_state.reg_teacher_name = ""
if "show_full_schedule" not in st.session_state:
    st.session_state.show_full_schedule = False
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "current_user_type" not in st.session_state:
    st.session_state.current_user_type = None
if "current_user_name" not in st.session_state:
    st.session_state.current_user_name = ""
if "show_teacher_registration" not in st.session_state:
    st.session_state.show_teacher_registration = False

# ----------------- Time Conversion Functions -----------------
def convert_to_24hour(time_str):
    """
    Convert 12-hour format time to 24-hour format
    Assumes: 
    - Times before 7:00 are PM (afternoon sessions)
    - Times 7:00 and after are AM (morning sessions)
    """
    try:
        if ':' in time_str:
            hours, minutes = time_str.split(':')
            hours = int(hours)
            minutes = int(minutes)
            
            # If time is before 7:00, assume it's PM (afternoon)
            if hours < 7:
                hours += 12
                
            return f"{hours:02d}:{minutes:02d}"
        else:
            return time_str
    except Exception:
        return time_str

def format_time_12hr(time_str):
    """
    Convert time string to 12-hour format with correct AM/PM
    Handles both 12-hour and 24-hour format inputs
    """
    try:
        # First convert to 24-hour format for consistent processing
        time_24hr = convert_to_24hour(time_str)
        
        if ':' in time_24hr:
            hours, minutes = time_24hr.split(':')
            hours = int(hours)
            minutes = int(minutes)
        else:
            return time_str  # Return original if format is wrong
        
        # Determine AM/PM and convert hours
        if hours == 0:
            period = "AM"
            display_hours = 12
        elif hours < 12:
            period = "AM"
            display_hours = hours
        elif hours == 12:
            period = "PM"
            display_hours = 12
        else:
            period = "PM"
            display_hours = hours - 12
        
        return f"{display_hours}:{minutes:02d} {period}"
    except Exception as e:
        return time_str  # Return original if parsing fails

def format_time_period(start_str, end_str):
    """Format a time period with correct AM/PM"""
    return f"{format_time_12hr(start_str)} - {format_time_12hr(end_str)}"

# ----------------- Background Reminder Checker -----------------
def schedule_checker(teacher_name: str, stop_event: Event):
    """
    Background loop that checks the timetable for upcoming classes for `teacher_name`.
    Uses `stop_event` to stop politely when requested.
    """
    while not stop_event.is_set():
        now = datetime.now()
        # Only check Monday-Friday
        if now.strftime("%A").upper() not in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"):
            stop_event.wait(600)
            continue

        # Read latest assignments for this teacher from session_state (so UI updates reflect)
        assignments_for_teacher = st.session_state.assignments.get(teacher_name, [])

        # Build mapping: class -> set(subjects)
        assigned_subjects_by_class = {}
        for a in assignments_for_teacher:
            try:
                cls = a["Class"]
                subj = a["Subject"].strip().upper()
                assigned_subjects_by_class.setdefault(cls, set()).add(subj)
            except Exception:
                continue

        current_day = now.strftime("%A").upper()

        for item in TIMETABLE:
            try:
                if item.get("Day", "").upper() != current_day:
                    continue
                class_name = item.get("Class")
                if not class_name:
                    continue
                item_subject_clean = (item.get("Subject") or "").strip().upper()
                if class_name not in assigned_subjects_by_class:
                    continue

                # handle multi-subject cells like "ENG/ELT"
                is_assigned = False
                for part in item_subject_clean.split("/"):
                    if part.strip() in assigned_subjects_by_class[class_name]:
                        is_assigned = True
                        break
                if not is_assigned:
                    continue

                start_time_str = item.get("StartTime")
                if not start_time_str:
                    continue
                # Convert to 24-hour for proper time comparison
                start_time_24hr = convert_to_24hour(start_time_str)
                start_time_obj = datetime.strptime(start_time_24hr, "%H:%M").time()
                start_dt_today = datetime.combine(now.date(), start_time_obj)
                reminder_time = start_dt_today - timedelta(minutes=NOTIFICATION_WINDOW_MINUTES)

                if reminder_time <= now < start_dt_today:
                    title = f"🔔 Class Alert ({format_time_12hr(start_time_str)})"
                    message = f"You have {item.get('Subject','').strip()} with {item.get('Class')} starting in {NOTIFICATION_WINDOW_MINUTES} minutes."

                    if PLYER_AVAILABLE:
                        try:
                            notification.notify(title=title, message=message, app_name="Sternfield Bot", timeout=10)
                        except Exception:
                            # ignore plyer errors in background thread
                            pass
                    else:
                        # fallback: write a line to Streamlit log area (can't reliably show in UI from thread)
                        print(f"[Reminder] {title} - {message}")
            except Exception:
                continue

        stop_event.wait(60)

# ----------------- Enhanced Schedule & Query Helpers -----------------
def get_full_day_schedule(teacher_name, day):
    """
    Returns a chronological list of time blocks for the day with:
      - StartTime (datetime.time)
      - EndTime (datetime.time)
      - Type: 'Teaching'|'Break'|'Free'
      - Subject/Class when applicable
      - Handles multiple classes in same period
    """
    assignments = st.session_state.assignments.get(teacher_name, [])
    if not TIMETABLE:
        return [], "No timetable data loaded."

    assigned_subjects_by_class = {}
    for a in assignments:
        try:
            assigned_subjects_by_class.setdefault(a["Class"], set()).add(a["Subject"].strip().upper())
        except Exception:
            continue

    all_periods_today = [
        p for p in TIMETABLE
        if p.get("Day", "").upper() == day.upper() and p.get("StartTime") and p.get("EndTime")
    ]
    if not all_periods_today:
        return [], "No timetable entries for that day."

    period_map = {}
    time_slots = set()
    for p in all_periods_today:
        key = (p["StartTime"], p["EndTime"])
        time_slots.add(key)
        period_map.setdefault(key, []).append(p)

    try:
        # Convert to 24-hour format for proper sorting
        sorted_slots = sorted(list(time_slots), key=lambda x: datetime.strptime(convert_to_24hour(x[0]), "%H:%M").time())
    except Exception:
        return [], "Time parsing error in timetable."

    full_schedule = []
    for start_raw, end_raw in sorted_slots:
        try:
            start_time_24hr = convert_to_24hour(start_raw)
            end_time_24hr = convert_to_24hour(end_raw)
            start_time_obj = datetime.strptime(start_time_24hr, "%H:%M").time()
            end_time_obj = datetime.strptime(end_time_24hr, "%H:%M").time()
        except Exception:
            continue

        # Check for multiple teaching assignments in the same period
        teaching_assignments = []
        for period in period_map.get((start_raw, end_raw), []):
            class_name = period.get("Class")
            item_subject_clean = (period.get("Subject") or "").strip().upper()
            if class_name in assigned_subjects_by_class:
                for part in item_subject_clean.split("/"):
                    if part.strip() in assigned_subjects_by_class[class_name]:
                        teaching_assignments.append({
                            "Class": class_name,
                            "Subject": period.get("Subject", "").strip()
                        })
                        break

        if teaching_assignments:
            # Handle multiple classes in same period
            if len(teaching_assignments) == 1:
                full_schedule.append({
                    "StartTime": start_time_obj,
                    "EndTime": end_time_obj,
                    "StartTimeStr": start_raw,
                    "EndTimeStr": end_raw,
                    "Type": "Teaching",
                    "Class": teaching_assignments[0]["Class"],
                    "Subject": teaching_assignments[0]["Subject"]
                })
            else:
                # Multiple classes - create a combined entry with proper class-subject pairing
                class_subject_pairs = []
                for ta in teaching_assignments:
                    class_subject_pairs.append(f"{ta['Subject']} with {ta['Class']}")
                
                # Remove duplicates while preserving order
                unique_pairs = []
                seen = set()
                for pair in class_subject_pairs:
                    if pair not in seen:
                        seen.add(pair)
                        unique_pairs.append(pair)
                
                classes_text = ", ".join(unique_pairs)
                full_schedule.append({
                    "StartTime": start_time_obj,
                    "EndTime": end_time_obj,
                    "StartTimeStr": start_raw,
                    "EndTimeStr": end_raw,
                    "Type": "Teaching",
                    "Class": "Multiple Classes",
                    "Subject": classes_text,
                    "Multiple": True,
                    "Details": teaching_assignments
                })
        else:
            # Check break/activity keywords
            is_break = False
            break_subject = ""
            for period in period_map.get((start_raw, end_raw), []):
                subj = (period.get("Subject") or "").upper()
                if any(k in subj for k in ("BREAK", "ASSEMBLY", "CLINIC", "TEA", "LIBRARY", "PRACTICAL", "CLUB", "SPORT", "LUNCH", "STUDY", "REMEDIAL")):
                    is_break = True
                    break_subject = period.get("Subject", "").strip()
                    break
            if is_break:
                full_schedule.append({
                    "StartTime": start_time_obj,
                    "EndTime": end_time_obj,
                    "StartTimeStr": start_raw,
                    "EndTimeStr": end_raw,
                    "Type": "Break",
                    "Subject": break_subject
                })
            else:
                full_schedule.append({
                    "StartTime": start_time_obj,
                    "EndTime": end_time_obj,
                    "StartTimeStr": start_raw,
                    "EndTimeStr": end_raw,
                    "Type": "Free"
                })

    # Sort by start time
    final_schedule = sorted(full_schedule, key=lambda x: x["StartTime"])
    return final_schedule, ""

def find_teacher_schedule(teacher_name, day, current_time_str):
    """
    Returns (current_lesson, next_lesson, status_message, free_periods_list)
    Enhanced to handle multiple classes in same period
    """
    if not TIMETABLE:
        return None, None, "No timetable loaded.", []

    try:
        # Convert current time to 24-hour format for comparison
        current_time_24hr = convert_to_24hour(current_time_str)
        current_time_obj = datetime.strptime(current_time_24hr, "%H:%M").time()
    except Exception:
        return None, None, "Invalid time format. Use HH:MM.", []

    full_schedule, status = get_full_day_schedule(teacher_name, day)
    if status:
        return None, None, status, []

    teaching_periods = [p for p in full_schedule if p["Type"] == "Teaching"]
    teaching_periods.sort(key=lambda x: x["StartTime"])

    current_lesson = None
    next_lesson = None

    for lesson in teaching_periods:
        start = lesson["StartTime"]
        end = lesson["EndTime"]
        if start <= current_time_obj < end:
            current_lesson = lesson
            continue
        if start > current_time_obj and next_lesson is None:
            next_lesson = lesson

    free_periods = [p for p in full_schedule if p["Type"] == "Free"]
    return current_lesson, next_lesson, "", free_periods

# ----------------- FIXED Student/Class Query Functions -----------------
def get_timetable_query_result(class_name, day, time_str=None):
    """
    Enhanced to handle:
    - No time provided (returns full day schedule)
    - Multiple activities in same period
    - Better time formatting
    """
    if not class_name or not day:
        return "Please select a Class and Day to check the schedule."
    
    # If no time provided, return full day schedule
    if not time_str:
        return get_full_class_schedule(class_name, day)
    
    try:
        # Convert query time to 24-hour format for comparison
        query_time_24hr = convert_to_24hour(time_str)
        query_time = datetime.strptime(query_time_24hr, "%H:%M").time()
    except Exception:
        return "Invalid time format. Please use HH:MM (e.g., 09:45)."

    found_activities = []
    for item in TIMETABLE:
        try:
            if item.get("Day", "").upper() == day.upper() and item.get("Class", "").upper() == class_name.upper():
                start_time_24hr = convert_to_24hour(item.get("StartTime", ""))
                end_time_24hr = convert_to_24hour(item.get("EndTime", ""))
                start = datetime.strptime(start_time_24hr, "%H:%M").time()
                end = datetime.strptime(end_time_24hr, "%H:%M").time()
                if start <= query_time < end:
                    found_activities.append(item)
        except Exception:
            continue

    if found_activities:
        if len(found_activities) == 1:
            activity = found_activities[0]
            time_display = format_time_period(activity.get('StartTime'), activity.get('EndTime'))
            subject = activity.get("Subject", "").strip()
            return (
                f"At **{format_time_12hr(time_str)}** on **{day.title()}** for **{class_name}**:\n\n"
                f"**Current Activity:** {subject}\n"
                f"**Time:** {time_display}\n"
                f"**Period:** {activity.get('Period', 'N/A')}"
            )
        else:
            # Multiple activities at same time
            result = f"At **{format_time_12hr(time_str)}** on **{day.title()}** for **{class_name}**:\n\n"
            result += "**Multiple activities found:**\n"
            for activity in found_activities:
                time_display = format_time_period(activity.get('StartTime'), activity.get('EndTime'))
                subject = activity.get("Subject", "").strip()
                result += f"• {subject} ({time_display})\n"
            return result
    else:
        return f"No scheduled activity found for **{class_name}** on **{day.title()}** at **{format_time_12hr(time_str)}**."

def get_full_class_schedule(class_name, day):
    """
    Returns the full day schedule for a specific class with correct time formatting
    FIXED: Handles 12-hour format times and sorts chronologically
    """
    if not class_name or not day:
        return "Please select a Class and Day."
    
    day_activities = []
    for item in TIMETABLE:
        try:
            if item.get("Day", "").upper() == day.upper() and item.get("Class", "").upper() == class_name.upper():
                # Get raw times
                start_time = item.get("StartTime", "")
                end_time = item.get("EndTime", "")
                subject = item.get("Subject", "").strip()
                
                # Convert 12-hour format times to 24-hour format for proper sorting
                start_time_24 = convert_to_24hour(start_time)
                end_time_24 = convert_to_24hour(end_time)
                
                # Convert to time objects for proper sorting
                start_time_obj = datetime.strptime(start_time_24, "%H:%M").time()
                end_time_obj = datetime.strptime(end_time_24, "%H:%M").time()
                
                day_activities.append({
                    "StartTime": start_time,
                    "EndTime": end_time,
                    "StartTimeObj": start_time_obj,
                    "EndTimeObj": end_time_obj,
                    "Subject": subject,
                    "Period": item.get("Period", "")
                })
        except Exception as e:
            continue

    if not day_activities:
        return f"No scheduled activities found for **{class_name}** on **{day.title()}**."

    # Sort by time using the time objects
    day_activities.sort(key=lambda x: x["StartTimeObj"])
    
    result = f"📅 **Full Schedule for {class_name} on {day.title()}:**\n\n"
    for activity in day_activities:
        time_slot = format_time_period(activity['StartTime'], activity['EndTime'])
        result += f"**{time_slot}**\n"
        result += f"• **Subject:** {activity['Subject']}\n"
        if activity.get('Period'):
            result += f"• **Period:** {activity['Period']}\n"
        result += "\n"
    
    return result

def get_class_subjects_only(class_name, day):
    """
    Returns only the list of subjects for a specific class on a given day
    """
    if not class_name or not day:
        return "Please select a Class and Day."
    
    subjects = set()
    for item in TIMETABLE:
        try:
            if item.get("Day", "").upper() == day.upper() and item.get("Class", "").upper() == class_name.upper():
                subject = item.get("Subject", "").strip()
                if subject:
                    # Handle multi-subject entries
                    for sub in subject.split("/"):
                        subjects.add(sub.strip())
        except Exception:
            continue

    if not subjects:
        return f"No subjects found for **{class_name}** on **{day.title()}**."

    subject_list = sorted(list(subjects))
    result = f"📚 **Subjects for {class_name} on {day.title()}:**\n\n"
    for i, subject in enumerate(subject_list, 1):
        result += f"{i}. {subject}\n"
    
    return result

# ----------------- Enhanced Chat Functions -----------------
def process_chat_message(message, user_type, user_name):
    """
    Process user messages and return bot responses
    Enhanced with better class schedule queries
    """
    message_lower = message.lower().strip()
    
    # Greeting patterns
    if any(word in message_lower for word in ["hello", "hi", "hey", "good morning", "good afternoon"]):
        return f"Hello {user_name}! I'm your Sternfield College assistant. How can I help you today?"
    
    # Registration queries
    if any(word in message_lower for word in ["register", "sign up", "setup", "add me", "new teacher"]):
        if user_type == "teacher":
            st.session_state.show_teacher_registration = True
            return f"Great {user_name}! I've opened the teacher registration section below. Please fill in your details to get started with personalized schedule alerts and information."
        else:
            return "Student registration isn't required! You can start asking questions about your schedule right away."
    
    # Class schedule queries (for students)
    if user_type == "student":
        # Pattern: "what subjects does [class] have on [day]"
        subject_pattern = r"(what subjects|subjects|subjects for|classes for)\s+(\w+(?:\s+\w+)*)\s+(on|for)\s+(\w+)"
        subject_match = re.search(subject_pattern, message_lower)
        if subject_match:
            class_name = subject_match.group(2).upper()
            day_name = subject_match.group(4).upper()
            return get_class_subjects_only(class_name, day_name)
        
        # Pattern: "schedule for [class] on [day]"
        schedule_pattern = r"(schedule|timetable|classes)\s+(for|of)\s+(\w+(?:\s+\w+)*)\s+(on|for)\s+(\w+)"
        schedule_match = re.search(schedule_pattern, message_lower)
        if schedule_match:
            class_name = schedule_match.group(3).upper()
            day_name = schedule_match.group(5).upper()
            return get_full_class_schedule(class_name, day_name)
    
    # Current period queries
    if any(word in message_lower for word in ["current", "now", "what period", "what class", "what's happening"]):
        return get_current_period_info(user_type, user_name)
    
    # Next period queries
    if any(word in message_lower for word in ["next", "after this", "following", "what's next"]):
        return get_next_period_info(user_type, user_name)
    
    # Schedule queries
    if any(word in message_lower for word in ["schedule", "timetable", "today", "day"]):
        return get_daily_schedule(user_type, user_name)
    
    # Free period queries
    if any(word in message_lower for word in ["free", "break", "free time", "when free"]):
        return get_free_periods(user_type, user_name)
    
    # Help
    if any(word in message_lower for word in ["help", "what can you do", "options"]):
        return get_help_message(user_type)
    
    # Default response
    return "I'm not sure I understand. Try asking about your current class, next period, today's schedule, or free periods. Type 'help' for more options."

def get_current_period_info(user_type, user_name):
    """Get information about current period - enhanced for multiple classes"""
    now = datetime.now()
    current_day = now.strftime("%A").upper()
    current_time = now.strftime("%H:%M")
    
    if user_type == "teacher":
        if user_name not in st.session_state.assignments:
            return f"I don't have your teaching assignments yet, {user_name}. Please register first by typing 'register' or clicking the 'Register as Teacher' button below."
        
        current_lesson, next_lesson, status, free_periods = find_teacher_schedule(user_name, current_day, current_time)
        
        if status:
            return f"Sorry, I couldn't check your schedule: {status}"
        elif current_lesson:
            if current_lesson.get("Multiple"):
                # Handle multiple classes - show the combined subject-class pairs
                return f"Your current classes: {current_lesson['Subject']} (until {format_time_12hr(current_lesson['EndTimeStr'])})"
            else:
                return f"Your current class is **{current_lesson['Subject']}** with **{current_lesson['Class']}** (until {format_time_12hr(current_lesson['EndTimeStr'])})"
        else:
            return "You don't have a teaching period right now. You're free!"
    
    else:  # student
        return "To check your current class, please tell me your class name and day, or use the Student Timetable Query tab."

def get_next_period_info(user_type, user_name):
    """Get information about next period - enhanced for multiple classes"""
    now = datetime.now()
    current_day = now.strftime("%A").upper()
    current_time = now.strftime("%H:%M")
    
    if user_type == "teacher":
        if user_name not in st.session_state.assignments:
            return f"I don't have your teaching assignments yet, {user_name}. Please register first by typing 'register' or clicking the 'Register as Teacher' button below."
        
        current_lesson, next_lesson, status, free_periods = find_teacher_schedule(user_name, current_day, current_time)
        
        if status:
            return f"Sorry, I couldn't check your schedule: {status}"
        elif next_lesson:
            if next_lesson.get("Multiple"):
                # Handle multiple classes - show the combined subject-class pairs
                return f"Your next classes at {format_time_12hr(next_lesson['StartTimeStr'])}: {next_lesson['Subject']}"
            else:
                return f"Your next class is **{next_lesson['Subject']}** with **{next_lesson['Class']}** at {format_time_12hr(next_lesson['StartTimeStr'])}"
        else:
            return "No more teaching periods scheduled for today!"
    
    else:  # student
        return "To check your next class, please tell me your class name and day, or use the Student Timetable Query tab."

def get_daily_schedule(user_type, user_name):
    """Get today's full schedule - enhanced for multiple classes"""
    now = datetime.now()
    current_day = now.strftime("%A").upper()
    
    if user_type == "teacher":
        if user_name not in st.session_state.assignments:
            return f"I don't have your teaching assignments yet, {user_name}. Please register first by typing 'register' or clicking the 'Register as Teacher' button below."
        
        full_schedule, status = get_full_day_schedule(user_name, current_day)
        
        if status:
            return f"Sorry, I couldn't get your schedule: {status}"
        
        schedule_text = f"Here's your schedule for {current_day.title()}:\n\n"
        for item in full_schedule:
            time_slot = format_time_period(item['StartTimeStr'], item['EndTimeStr'])
            if item["Type"] == "Teaching":
                if item.get("Multiple"):
                    # For multiple classes, display the combined subject-class pairs
                    schedule_text += f"• {time_slot}: 👨‍🏫 {item['Subject']}\n"
                else:
                    schedule_text += f"• {time_slot}: 👨‍🏫 {item['Subject']} with {item['Class']}\n"
            elif item["Type"] == "Break":
                schedule_text += f"• {time_slot}: ☕ {item.get('Subject', 'Break')}\n"
            else:
                schedule_text += f"• {time_slot}: ✅ Free Period\n"
        
        return schedule_text
    
    else:  # student
        return "To see your full schedule, please tell me your class name or use the Student Timetable Query tab."

def get_free_periods(user_type, user_name):
    """Get free periods for the day"""
    now = datetime.now()
    current_day = now.strftime("%A").upper()
    current_time = now.strftime("%H:%M")
    
    if user_type == "teacher":
        if user_name not in st.session_state.assignments:
            return f"I don't have your teaching assignments yet, {user_name}. Please register first by typing 'register' or clicking the 'Register as Teacher' button below."
        
        current_lesson, next_lesson, status, free_periods = find_teacher_schedule(user_name, current_day, current_time)
        
        if status:
            return f"Sorry, I couldn't check your schedule: {status}"
        
        if free_periods:
            free_text = "Your free periods today:\n"
            for period in free_periods:
                if period['EndTime'] > datetime.strptime(convert_to_24hour(current_time), "%H:%M").time():
                    free_text += f"• {format_time_period(period['StartTimeStr'], period['EndTimeStr'])}\n"
            return free_text
        else:
            return "No free periods found in your schedule today."
    
    else:  # student
        return "Free period information is currently available for teachers. Students can check their schedule in the Student Timetable Query tab."

def get_help_message(user_type):
    """Get help message based on user type - enhanced for students"""
    if user_type == "teacher":
        return """I can help you with:
• **Register** - Set up your teacher profile and class assignments
• **Current period** - What you're teaching right now
• **Next period** - Your upcoming class
• **Today's schedule** - Your full schedule for today
• **Free periods** - When you have free time today

*Type 'register' or click the button below to get started!*"""
    else:
        return """I can help you with:
• **"What subjects does [class] have on [day]"** - See all subjects for a class
• **"Schedule for [class] on [day]"** - Full daily schedule
• **Current period** - What's happening in your class right now
• Use the Student Timetable Query tab for detailed schedule lookups
• Or ask general questions about the school timetable!

**Examples:**
- "What subjects does Form 1 have on Monday?"
- "Schedule for Form 2 on Tuesday"
- "What's happening now for Form 3?"""

# ----------------- Quick Teacher Registration -----------------
def quick_teacher_registration():
    """A simplified teacher registration form that can be embedded in the chat interface"""
    st.subheader("🚀 Quick Teacher Registration")
    
    with st.form("quick_teacher_registration"):
        teacher_name = st.text_input("Your Full Name", value=st.session_state.current_user_name, 
                                   placeholder="Enter your name as you want it to appear")
        
        st.write("**Step 1: Tell me what classes you teach**")
        if TIMETABLE:
            all_classes = sorted({item.get("Class") for item in TIMETABLE if item.get("Class")})
            all_subjects = sorted({(item.get("Subject") or "").strip() for item in TIMETABLE if item.get("Subject")})
            
            selected_class = st.selectbox("Select Class", options=[""] + all_classes, key="quick_class")
            selected_subject = st.selectbox("Select Subject", options=[""] + all_subjects, key="quick_subject")
        
        submitted = st.form_submit_button("Complete Registration")
        
        if submitted:
            if not teacher_name.strip():
                st.error("Please enter your name")
                return False
                
            if not selected_class or not selected_subject:
                st.error("Please select both a class and subject")
                return False
            
            # Save the assignment
            st.session_state.assignments.setdefault(teacher_name, [])
            new_assignment = {"Class": selected_class, "Subject": selected_subject}
            
            if new_assignment not in st.session_state.assignments[teacher_name]:
                st.session_state.assignments[teacher_name].append(new_assignment)
                save_assignments(st.session_state.assignments)
                st.session_state.current_user_name = teacher_name
                st.session_state.show_teacher_registration = False
                st.success(f"🎉 Welcome {teacher_name}! You're now registered. You can add more class assignments in the Teacher Setup tab.")
                st.rerun()
                return True
            else:
                st.warning("This Class/Subject assignment already exists.")
                return False
    return False

# ----------------- Chat Interface -----------------
def chat_interface():
    st.header("💬 Sternfield College Chat Assistant")
    
    # User type selection
    col1, col2 = st.columns(2)
    with col1:
        user_type = st.selectbox(
            "I am a:",
            ["Student", "Teacher"],
            key="chat_user_type"
        )
    with col2:
        user_name = st.text_input(
            "Your name:",
            value=st.session_state.current_user_name,
            placeholder="Enter your name here...",
            key="chat_user_name"
        )
    
    # Update session state
    if user_name:
        st.session_state.current_user_name = user_name
        st.session_state.current_user_type = user_type.lower()
    
    # Quick registration button for teachers
    if user_type == "Teacher" and user_name and user_name not in st.session_state.assignments:
        st.info("🔔 **Teacher Alert**: You need to register to access personalized schedule information and alerts!")
        if st.button("🎯 Register as Teacher", type="primary", key="quick_reg_btn"):
            st.session_state.show_teacher_registration = True
            st.rerun()
    
    st.markdown("---")
    
    # Display chat history
    chat_container = st.container()
    with chat_container:
        for message in st.session_state.chat_history:
            if message["role"] == "user":
                st.markdown(f"**{message['user']}:** {message['content']}")
            else:
                st.markdown(f"**Bot:** {message['content']}")
            st.markdown("---")
    
    # Teacher registration section (conditionally displayed)
    if st.session_state.show_teacher_registration:
        quick_teacher_registration()
        st.markdown("---")
    
    # Chat input
    user_input = st.text_input("Type your message here...", key="chat_input", 
                              placeholder="Ask me about your schedule, classes, or type 'help' for options")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("Send Message", type="primary", key="send_chat") and user_input and user_name:
            # Add user message to history
            st.session_state.chat_history.append({
                "role": "user",
                "user": user_name,
                "content": user_input
            })
            
            # Get bot response
            bot_response = process_chat_message(user_input, st.session_state.current_user_type, user_name)
            
            # Add bot response to history
            st.session_state.chat_history.append({
                "role": "bot",
                "user": "Bot",
                "content": bot_response
            })
            
            # Clear input and rerun
            st.rerun()
    
    with col2:
        if st.button("Clear Chat", key="clear_chat"):
            st.session_state.chat_history = []
            st.rerun()

# ----------------- Enhanced Student Query Interface -----------------
def student_query_interface():
    st.header("📚 Student/General Timetable Query")
    st.write("Find out what's happening in any class - with or without specific time!")
    
    if not TIMETABLE:
        st.warning("Timetable data failed to load.")
        return

    all_classes = sorted({item.get("Class") for item in TIMETABLE if item.get("Class")})
    day_options = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]

    today_name = datetime.now().strftime("%A").upper()
    default_day_index = day_options.index(today_name) if today_name in day_options else 0
    current_time_str = datetime.now().strftime("%H:%M")

    # Query type selection
    query_type = st.radio(
        "What would you like to check?",
        ["Check specific time", "Full day schedule", "List of subjects only"],
        horizontal=True
    )

    col1, col2 = st.columns(2)
    with col1:
        selected_class = st.selectbox("Select Class", options=[""] + list(all_classes), key="query_class")
    with col2:
        selected_day = st.selectbox("Select Day", options=day_options, index=default_day_index, key="query_day")

    # Time input only for specific time queries
    if query_type == "Check specific time":
        time_input = st.text_input("Enter Time (HH:MM)", value=current_time_str, key="query_time",
                                 help="Leave empty to see current time schedule")
    else:
        time_input = None

    st.markdown("---")
    
    if st.button("🔍 Get Information", type="primary", key="get_schedule_btn"):
        if selected_class and selected_day:
            if query_type == "Check specific time":
                result = get_timetable_query_result(selected_class, selected_day, time_input)
            elif query_type == "Full day schedule":
                result = get_full_class_schedule(selected_class, selected_day)
            else:  # List of subjects only
                result = get_class_subjects_only(selected_class, selected_day)
            
            st.success("Query Result:")
            st.markdown(result)
        else:
            st.error("Please select both Class and Day.")

# ----------------- Streamlit UI -----------------
def teacher_registration():
    st.header("🍎 Teacher Registration & Setup")
    st.write("Register all your classes and manage your teaching assignments.")
    
    if not TIMETABLE:
        st.warning("Timetable data not loaded; cannot register.")
        return

    all_subjects = sorted({(item.get("Subject") or "").strip() for item in TIMETABLE if item.get("Subject")})
    all_classes = sorted({item.get("Class") for item in TIMETABLE if item.get("Class")})

    # Name input (persisted)
    st.session_state.reg_teacher_name = st.text_input("1. Your Name", 
                                                     value=st.session_state.reg_teacher_name, 
                                                     placeholder="Enter your full name",
                                                     key="name_input").strip().title()
    teacher_name = st.session_state.reg_teacher_name

    if teacher_name:
        st.success(f"Welcome, {teacher_name}! 👋")
        st.subheader(f"2. Your Teaching Assignments")
        
        # Show current assignments
        current_assignments = st.session_state.assignments.get(teacher_name, [])
        if current_assignments:
            st.write("**Your current assignments:**")
            for i, assignment in enumerate(current_assignments):
                col1, col2, col3 = st.columns([3, 2, 1])
                col1.write(f"• {assignment.get('Subject')} for {assignment.get('Class')}")
                if col3.button("Remove", key=f"remove_{i}"):
                    st.session_state.assignments[teacher_name].pop(i)
                    if not st.session_state.assignments[teacher_name]:
                        del st.session_state.assignments[teacher_name]
                    save_assignments(st.session_state.assignments)
                    st.success("Assignment removed!")
                    st.rerun()
        else:
            st.info("No assignments yet. Add your first assignment below!")
        
        # Add new assignment
        with st.form("assignment_form"):
            st.markdown("**Add New Teaching Assignment:**")
            col1, col2 = st.columns(2)
            with col1:
                selected_class = st.selectbox("Select Class", options=[""] + all_classes, key="reg_class")
            with col2:
                selected_subject = st.selectbox("Select Subject", options=[""] + all_subjects, key="reg_subject")
            
            register_button = st.form_submit_button("➕ Add This Assignment")
            
            if register_button:
                if selected_class and selected_subject:
                    st.session_state.assignments.setdefault(teacher_name, [])
                    new_assignment = {"Class": selected_class, "Subject": selected_subject}
                    if new_assignment not in st.session_state.assignments[teacher_name]:
                        st.session_state.assignments[teacher_name].append(new_assignment)
                        save_assignments(st.session_state.assignments)
                        st.success(f"✅ Added: {selected_subject} for {selected_class}.")
                        st.rerun()
                    else:
                        st.warning("This Class/Subject assignment already exists.")
                else:
                    st.error("Please select both Class and Subject.")

    st.markdown("---")
    st.subheader("All Registered Teachers")
    teachers = sorted(list(st.session_state.assignments.keys()))
    if teachers:
        st.write(", ".join(teachers))
    else:
        st.info("No teachers registered yet.")

def teacher_bot_interface():
    st.header("🗓️ Teacher Timetable Bot - Your Assistant")
    if not st.session_state.assignments:
        st.warning("No teachers registered yet. Please register in the 'Teacher Setup' tab first.")
        st.info("💡 **Quick tip**: You can also register directly from the Chat Assistant tab!")
        return

    teacher_options = sorted(list(st.session_state.assignments.keys()))
    selected_teacher = st.selectbox("👋 Hello! Who are you?", options=[""] + teacher_options, key="bot_teacher")

    if not selected_teacher:
        return

    st.success(f"Welcome back, {selected_teacher}! 🎉")
    st.subheader(f"Schedule Query for {selected_teacher}")

    # Thread control: stop previous checker if different teacher
    if st.session_state.last_checked_teacher != selected_teacher:
        if st.session_state.checker_stop_event is not None:
            try:
                st.session_state.checker_stop_event.set()
            except Exception:
                pass

        stop_event = Event()
        st.session_state.checker_stop_event = stop_event
        t = Thread(target=schedule_checker, args=(selected_teacher, stop_event), daemon=True)
        t.start()
        st.session_state.checker_thread = t
        st.session_state.last_checked_teacher = selected_teacher
        st.success(f"🔔 Background reminder service activated for {selected_teacher}")

    col1, col2 = st.columns(2)
    day_options = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
    today_name = datetime.now().strftime("%A").upper()
    default_day_index = day_options.index(today_name) if today_name in day_options else 0
    with col1:
        selected_day = st.selectbox("📆 Which day are we checking?", options=day_options, index=default_day_index, key="bot_day")
    with col2:
        current_time_str = datetime.now().strftime("%H:%M")
        time_input = st.text_input("⏰ What time should I check? (HH:MM)", value=current_time_str, key="bot_time")

    st.markdown("---")
    if st.button(f"🔍 Show My Full {selected_day.title()} Schedule", type="primary"):
        st.session_state.show_full_schedule = True

    st.subheader("Bot Response:")
    try:
        current, next_lesson, status, free_periods = find_teacher_schedule(selected_teacher, selected_day, time_input)
        current_time_display = format_time_12hr(time_input)
        if status:
            st.info(f"Bot: {status}")
        elif current:
            st.success(f"Bot: At {current_time_display} on {selected_day.title()}:")
            if current.get("Multiple"):
                st.markdown(f"**You have multiple classes:** {current['Subject']}")
            else:
                st.markdown(f"**Current class:** {current.get('Subject')} with {current.get('Class')}")
        else:
            st.info(f"Bot: You are currently FREE at {current_time_display}.")

        if next_lesson:
            st.warning("Bot: Your next lesson:")
            if next_lesson.get("Multiple"):
                st.markdown(f"**Multiple classes:** {next_lesson['Subject']} at {format_time_12hr(next_lesson['StartTimeStr'])}")
            else:
                st.markdown(f"**{next_lesson.get('Subject')}** with **{next_lesson.get('Class')}** at {format_time_12hr(next_lesson['StartTimeStr'])}")
        else:
            st.info("Bot: No further teaching lessons scheduled for today.")

        try:
            now_obj = datetime.strptime(convert_to_24hour(time_input), "%H:%M").time()
            free_periods_str = [
                format_time_period(p['StartTimeStr'], p['EndTimeStr'])
                for p in free_periods
                if p['EndTime'] > now_obj
            ]
        except Exception:
            free_periods_str = []

        if free_periods_str:
            st.markdown("Bot: Your remaining true free time slots today are:")
            st.code("\n".join(free_periods_str))
    except ValueError:
        st.error("Bot: Invalid time format. Please use HH:MM (e.g., 08:30).")

    if st.session_state.show_full_schedule:
        full_schedule, status = get_full_day_schedule(selected_teacher, selected_day)
        st.markdown("---")
        st.markdown(f"## 📝 Full {selected_day.title()} Schedule:")
        if status:
            st.warning(status)
        elif full_schedule:
            schedule_data = []
            for item in full_schedule:
                time_slot = format_time_period(item['StartTimeStr'], item['EndTimeStr'])
                if item["Type"] == "Teaching":
                    if item.get("Multiple"):
                        activity = f"👨‍🏫 {item['Subject']}"
                    else:
                        activity = f"👨‍🏫 {item['Subject']} with {item['Class']}"
                elif item["Type"] == "Break":
                    activity = f"☕ {item.get('Subject', 'Break')}"
                else:
                    activity = "✅ FREE PERIOD"
                schedule_data.append({"Time Slot": time_slot, "Activity": activity})
            st.table(schedule_data)
        else:
            st.info("No activities found for this day.")

# ----------------- Main -----------------
def main():
    st.set_page_config(page_title="Sternfield Timetable Bot", layout="wide")
    st.title("Sternfield College Timetable Assistant 🏫")
    
    # Add a welcome message with clear navigation
    st.sidebar.success("💡 **Quick Start Guide**")
    st.sidebar.markdown("""
    **For Teachers:**
    1. Go to **Chat Assistant** tab
    2. Select "Teacher" and enter your name
    3. Click "Register as Teacher" button
    4. Add your class assignments
    
    **For Students:**
    1. Go to **Chat Assistant** tab  
    2. Select "Student" and enter your name
    3. Ask about schedules or use the Query tab
    
    **New Features:**
    • Multiple classes in same period
    • Correct 12-hour time format
    • Full day schedules without time
    • Subjects-only queries
    """)

    tab1, tab2, tab3, tab4 = st.tabs(["Chat Assistant 💬", "Teacher Bot 🤖", "Teacher Setup 📝", "Student Timetable Query 📚"])
    with tab1:
        chat_interface()
    with tab2:
        teacher_bot_interface()
    with tab3:
        teacher_registration()
    with tab4:
        student_query_interface()

if __name__ == "__main__":
    main()
