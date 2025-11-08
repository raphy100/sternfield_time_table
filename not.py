from plyer import notification
import time

print("Testing notification...")
notification.notify(
    title='Test Notification',
    message='This confirms plyer is installed and working!',
    app_name='Notification Tester',
    timeout=5  # Display for 5 seconds
)
time.sleep(6) # Wait for the notification to potentially clear
print("Test complete.")