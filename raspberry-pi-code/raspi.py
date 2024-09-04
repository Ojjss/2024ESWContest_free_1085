import time
import Adafruit_ADS1x15
import RPi.GPIO as GPIO
import threading
from RPLCD.i2c import CharLCD
import requests
import gpsd
import socket
import netifaces  # netifaces 라이브러리 추가

# GPIO 설정
GPIO.setmode(GPIO.BCM)
RELAY_PIN = 16  # GPIO16에 연결된 릴레이 제어 핀
GPIO.setup(RELAY_PIN, GPIO.OUT)

# ADC 설정
adc = Adafruit_ADS1x15.ADS1115(address=0x4a, busnum=1)
adc1 = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)
adc2 = Adafruit_ADS1x15.ADS1115(address=0x49, busnum=1)
GAIN = 1

# MQ2 센서 채널 설정
mq135_channel = 0  # MQ135 센서를 A0에 연결

# FSR408 압력 센서 채널 설정
pressure_channels_adc1 = [0, 1, 2, 3]
pressure_channels_adc2 = [0, 1, 2, 3]

# 임계값 설정 (테스트 및 보정 필요)
BREATH_THRESHOLD = 150  # 입김 감지 기준 (MQ2 센서)
ALCOHOL_THRESHOLD = 150  # 음주 상태 판단 기준 (MQ2 센서)
NO_DETECTION_TIMEOUT = 5  # 감지되지 않으면 5초 후에 경고

# 초음파 센서 설정
SENSORS = [
    {'name': 'Left Front', 'TRIG': 22, 'ECHO': 10},
    {'name': 'Left Middle', 'TRIG': 17, 'ECHO': 27},
    {'name': 'Left Rear', 'TRIG': 23, 'ECHO': 24},
    {'name': 'Right Front', 'TRIG': 5, 'ECHO': 6},
    {'name': 'Right Middle', 'TRIG': 13, 'ECHO': 19},
    {'name': 'Right Rear', 'TRIG': 26, 'ECHO': 21},
]

# 각 초음파 센서 설정
for sensor in SENSORS:
    GPIO.setup(sensor['TRIG'], GPIO.OUT)
    GPIO.setup(sensor['ECHO'], GPIO.IN)
    GPIO.output(sensor['TRIG'], False)

print("초음파 센서 초기화 완료")
time.sleep(3)

# I2C LCD 설정
lcd = CharLCD('PCF8574', 0x27, cols=16, rows=2)

# 측정 결과를 저장할 딕셔너리
distances = {sensor['name']: 0 for sensor in SENSORS}
lock = threading.Lock()

# GPSD에 연결
def setup_gps():
    gpsd.connect()

def get_gps_data():
    try:
        packet = gpsd.get_current()
        latitude = packet.lat
        longitude = packet.lon
        return latitude, longitude
    except Exception as e:
        print(f"GPS 데이터 가져오기 중 오류 발생: {e}")
        return None, None

# 라즈베리 파이의 IP 주소 가져오기
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('10.254.254.254', 1))  # 임의의 외부 IP 주소에 연결 시도 (실제 연결되지 않음)
        ip_address = s.getsockname()[0]
        return ip_address
    except Exception as e:
        print(f"IP 주소를 가져오는 중 오류 발생: {e}")
        return None

# 라즈베리 파이의 MAC 주소 가져오기
def get_mac_address(interface='wlan0'):
    try:
        mac = netifaces.ifaddresses(interface)[netifaces.AF_LINK][0]['addr']
        return mac
    except KeyError:
        return None

# 기존 send_data 함수를 수정하여 위치 데이터를 추가로 전송
def send_data(event, value):
    url = "http://127.0.0.1:8080/api/sensor"  # 서버 IP와 포트를 설정하세요
    latitude, longitude = get_gps_data()  # GPS 데이터 가져오기
    local_ip = get_local_ip()  # 라즈베리 파이의 IP 주소 가져오기
    mac_address = get_mac_address('wlan0')  # MAC 주소 가져오기 (인터페이스 이름 변경 가능)
    data = {
        "event": event,
        "value": value,
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "latitude": latitude,
        "longitude": longitude,
        "ip": local_ip,  # IP 주소를 데이터에 추가
        "mac": mac_address  # MAC 주소 추가
    }
    try:
        response = requests.post(url, json=data, timeout=20)  # 타임아웃 20초 설정
        if response.status_code == 200:
            print("데이터 전송 성공")
        else:
            print(f"데이터 전송 실패: {response.status_code}")
    except requests.exceptions.Timeout:
        print("요청 시간 초과. 서버가 응답하지 않습니다.")
    except requests.exceptions.RequestException as e:
        print(f"요청 오류: {e}")

def read_mq135():
    return adc.read_adc(mq135_channel, gain=GAIN)

def measure_distance(sensor):
    global distances
    while True:
        GPIO.output(sensor['TRIG'], True)
        time.sleep(0.00001)
        GPIO.output(sensor['TRIG'], False)

        start = time.time()
        while GPIO.input(sensor['ECHO']) == 0:
            start = time.time()

        while GPIO.input(sensor['ECHO']) == 1:
            stop = time.time()

        elapsed = stop - start
        distance = elapsed * 34300 / 2

        with lock:
            distances[sensor['name']] = distance

        time.sleep(0.5)

# 각 센서마다 스레드를 생성하여 거리 측정
threads = []
for sensor in SENSORS:
    thread = threading.Thread(target=measure_distance, args=(sensor,))
    thread.daemon = True
    thread.start()
    threads.append(thread)

def measure_pressure():
    total_pressure = 0
   
    # 첫 번째 ADC에서 값 읽기
    for channel in pressure_channels_adc1:
        value = adc1.read_adc(channel, gain=GAIN)
        total_pressure += value

    # 두 번째 ADC에서 값 읽기
    for channel in pressure_channels_adc2:
        value = adc2.read_adc(channel, gain=GAIN)
        total_pressure += value

    return total_pressure

def detect_breath_and_alcohol():
    initial_value = read_mq135()
    print(f"Initial MQ135 Sensor Value: {initial_value}")
   
    start_time = time.time()
    while True:
        mq135_value = read_mq135()
        print(f"Current MQ135 Sensor Value: {mq135_value}")  # 현재 값을 출력

        # 입김 감지 (입김 감지 임계값을 기준으로)
        if (mq135_value < initial_value - BREATH_THRESHOLD):
            return 'breath_detected'

        # 알코올 감지 (초기값 + 알코올 임계값보다 큰 경우)
        if (mq135_value > initial_value + ALCOHOL_THRESHOLD):
            return 'alcohol_detected'
        
        # 감지가 되지 않은 상태로 5초 경과
        if (time.time() - start_time > NO_DETECTION_TIMEOUT):
            return 'no_breath_detected'

        time.sleep(0.1)  # 측정 간격

def monitor_distance_for_duration(duration=15):
    start_time = time.time()
    while time.time() - start_time < duration:
        with lock:
            total_distance = sum(distances.values())
            if not (125 <= total_distance <= 145):
                return False
        time.sleep(1)  # 1초 간격으로 측정
    return True

def main():
    setup_gps()  # GPS 설정
    try:
        while True:
            # 상태 변수 초기화
            state = 0
            initial_time = None
            initial_state = True
            a = None
            b = None
            GPIO.output(RELAY_PIN, GPIO.LOW)

            lcd.clear()
            lcd.write_string("blow")
            time.sleep(0.5)

            # 입김 및 알코올 감지
            detection_result = detect_breath_and_alcohol()

            if detection_result == 'breath_detected':
                lcd.clear()
                lcd.write_string("Breath detected!")
                print("Breath detected!")
                
            
            elif detection_result == 'alcohol_detected':
                lcd.clear()
                lcd.write_string("Alcohol detected")
                GPIO.output(RELAY_PIN, GPIO.LOW)  # 알코올 감지 후 출력 핀 LOW로 리셋
                send_data('alcohol_detected', 1)
                print("Alcohol detected, restarting detection process.")
                continue  # 알코올 감지 후 다시 입김 감지 단계로 돌아가기
            
            elif detection_result == 'no_breath_detected':
                lcd.clear()
                lcd.write_string("Please try again")
                GPIO.output(RELAY_PIN,GPIO.LOW)
                print("Continuing execution after no breath detection.")
                continue  # 바람이 감지될 때까지 계속 반복

            # 초음파 및 압력 센서 측정
            while True:
                with lock:
                    total_distance = sum(distances.values())
                    print(f"Total Distance Sum: {total_distance:.2f} cm")

                    if state == 0:
                        if initial_state:
                            initial_time = time.time()
                            initial_state = False

                        if total_distance <= 90:
                            if time.time() - initial_time >= 0.5:
                                state = 1
                                a = total_distance
                                GPIO.output(RELAY_PIN, GPIO.HIGH)
                                print(f"State = 1, a = {a:.2f}")
                        else:
                            initial_state = True

                    elif state == 1 and total_distance <= (a):
                        state = 2
                        b = measure_pressure()
                        print(f"State = 2, b = {b:.2f}")

                time.sleep(0.3)

                if state == 2:
                    current_pressure = measure_pressure()
                    print(f"Pressure: {current_pressure:.2f}")

                    if current_pressure >= (b + 15000):
                        lcd.clear()
                        lcd.write_string("overstaffing")
                        GPIO.output(RELAY_PIN, GPIO.LOW)  # 2명 이상 탑승 시 GPIO 핀 16에 LOW 신호 출력
                        send_data('overstaffing', 1)
                        print("overstaffing")
                        break  # 입김 감지 단계로 돌아가기 위해 현재 루프 탈출
                    elif 0 <= current_pressure <= b + 15000:
                        # 현재 압력이 b ± 15000 범위 내에 있는 경우
                        lcd.clear()
                        lcd.write_string("Lets move !")
                        GPIO.output(RELAY_PIN, GPIO.HIGH)  # GPIO 핀 16에 HIGH 신호 출력
                        print("Pressure within b ± 10000 range, monitoring distance...")
                        if monitor_distance_for_duration():
                            lcd.clear()
                            lcd.write_string("Please wait. . .")
                            GPIO.output(RELAY_PIN, GPIO.LOW)
                            print("Distance maintained within range for 5 minutes, restarting breath detection.")
                            break  # 입김 감지 단계로 돌아가기 위해 현재 루프 탈출

    except KeyboardInterrupt:
        print("프로그램 종료")
        GPIO.cleanup()

if __name__ == "__main__":
    main()
