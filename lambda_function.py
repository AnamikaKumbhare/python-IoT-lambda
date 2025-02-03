import json
import os
import boto3
import shutil
from zipfile import ZipFile
import subprocess
from pathlib import Path
import uuid
import traceback

# Initialize AWS clients
s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

def log_error(error, context=""):
    """Helper function to log errors with stack trace"""
    print(f"ERROR in {context}:")
    print(f"Error message: {str(error)}")
    print("Stack trace:")
    print(traceback.format_exc())

def run_command(command, work_dir=None, env=None):
    """Execute a shell command and handle its output"""
    try:
        print(f"Executing command: {command}")
        print(f"Working directory: {work_dir}")
        print(f"Environment: {env}")
        
        process = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True
        )
        
        if process.stdout:
            print('stdout:', process.stdout)
        if process.stderr:
            print('stderr:', process.stderr)
            
        if process.returncode != 0:
            raise Exception(f"Command failed with code {process.returncode}: {process.stderr or process.stdout}")
        
        return process.stdout.strip()
    except Exception as e:
        log_error(e, f"Command execution: {command}")
        raise

def download_and_extract_arduino_cli(temp_dir):
    """Download and extract Arduino CLI from S3"""
    try:
        print(f"Starting Arduino CLI download to {temp_dir}")
        
        # Verify environment variables
        required_env_vars = ['ARDUINO_BUCKET', 'ARDUINO_KEY']
        missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
        if missing_vars:
            raise Exception(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        zip_path = os.path.join(temp_dir, 'arduino-cli.zip')
        extract_path = os.path.join(temp_dir, 'arduino-cli')
        
        print('Downloading Arduino CLI from S3...')
        response = s3.get_object(
            Bucket=os.environ['ARDUINO_BUCKET'],
            Key=os.environ['ARDUINO_KEY']
        )
        
        with open(zip_path, 'wb') as f:
            f.write(response['Body'].read())
        
        os.makedirs(extract_path, exist_ok=True)
        
        print('Extracting Arduino CLI...')
        with ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
        
        # Clean up zip file
        os.remove(zip_path)
        
        arduino_cli_bin = os.path.join(extract_path, 'opt', 'arduino-cli', 'bin', 'arduino-cli')
        print(f'Setting permissions for: {arduino_cli_bin}')
        
        os.chmod(arduino_cli_bin, 0o755)
        
        source_esp = os.path.join(extract_path, 'opt', 'arduino-cli', 'hardware', 'esp8266')
        target_esp = os.path.join(temp_dir, 'arduino', 'user', 'hardware', 'esp8266')
        
        if os.path.exists(source_esp):
            print('Copying ESP8266 files to user directory...')
            os.makedirs(os.path.dirname(target_esp), exist_ok=True)
            shutil.copytree(source_esp, target_esp, dirs_exist_ok=True)
        
        return os.path.join(extract_path, 'opt', 'arduino-cli')
    except Exception as e:
        log_error(e, "Arduino CLI setup")
        raise

def initialize_esp8266_platform(arduino_cli, config_path, env):
    """Initialize the ESP8266 platform"""
    try:
        print('Updating core index...')
        run_command(
            f'{arduino_cli}/bin/arduino-cli core update-index --config-file "{config_path}"',
            env=env
        )

        print('Installing ESP8266 platform...')
        run_command(
            f'{arduino_cli}/bin/arduino-cli core install esp8266:esp8266 --config-file "{config_path}"',
            env=env
        )

        installed = run_command(
            f'{arduino_cli}/bin/arduino-cli core list --config-file "{config_path}"',
            env=env
        )
        print('Installed cores:', installed)
        
        if 'esp8266:esp8266' not in installed:
            raise Exception("ESP8266 platform installation verification failed")
            
    except Exception as e:
        log_error(e, "ESP8266 platform initialization")
        raise

def generate_arduino_code(ssid, password, device_id, api_endpoint="http://192.168.1.7:8000/api/pollution-data"):
    """Generate Arduino code with the provided configuration"""
    try:
        # Use triple quotes for better multiline string handling
        return f'''#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>

// WiFi credentials
const char* ssid = "{ssid}";
const char* password = "{password}";
const char* deviceId = "{device_id}";
const char* apiEndpoint = "{api_endpoint}";

// Sensor calibration values
const float VOLTAGE_REF = 3.3;    // Reference voltage
const int ADC_RESOLUTION = 1023;  // 10-bit ADC
const float R0 = 10.0;            // Sensor resistance in clean air
const float RL = 10.0;            // Load resistance in kΩ

// Function declarations
float convertToVoltage(int rawValue);
float convertToPPM(float voltage);
void connectWiFi();
bool sendData(float ppm, float voltage, int rawValue);

void setup() {{
    Serial.begin(9600);
    Serial.println("\\n\\n=== Air Pollution Monitor Starting ===");
    
    pinMode(A0, INPUT);
    connectWiFi();
}}

void loop() {{
    Serial.println("\\n=== New Pollution Reading Cycle ===");
    
    if (WiFi.status() != WL_CONNECTED) {{
        Serial.println("WiFi disconnected. Reconnecting...");
        connectWiFi();
        return;
    }}
    
    int rawValue = analogRead(A0);
    float voltage = convertToVoltage(rawValue);
    float ppm = convertToPPM(voltage);
    
    Serial.println("Sensor Readings:");
    Serial.printf("Raw Value: %d\\n", rawValue);
    Serial.printf("Voltage: %.2fV\\n", voltage);
    Serial.printf("Pollution Level: %.2f PPM\\n", ppm);
    
    if (sendData(ppm, voltage, rawValue)) {{
        Serial.println("Data sent successfully");
    }} else {{
        Serial.println("Failed to send data");
    }}
    
    const int READING_INTERVAL = 30000;
    Serial.printf("Waiting %d seconds for next reading...\\n", READING_INTERVAL/1000);
    delay(READING_INTERVAL);
}}

float convertToVoltage(int rawValue) {{
    return (rawValue * VOLTAGE_REF) / ADC_RESOLUTION;
}}

float convertToPPM(float voltage) {{
    float vRL = voltage;
    float Rs = ((VOLTAGE_REF * RL) / vRL) - RL;
    
    float ratio = Rs / R0;
    float ppm = 116.6020682 * pow(ratio, -2.769034857);
    
    return ppm;
}}

void connectWiFi() {{
    Serial.printf("Connecting to WiFi network: %s\\n", ssid);
    WiFi.begin(ssid, password);
    
    int attempts = 0;
    const int MAX_ATTEMPTS = 20;
    
    while (WiFi.status() != WL_CONNECTED && attempts < MAX_ATTEMPTS) {{
        delay(500);
        Serial.print(".");
        attempts++;
    }}
    
    if (WiFi.status() == WL_CONNECTED) {{
        Serial.println("\\nWiFi Connected!");
        Serial.printf("IP Address: %s\\n", WiFi.localIP().toString().c_str());
    }} else {{
        Serial.println("\\nWiFi connection failed. Restarting...");
        ESP.restart();
    }}
}}

bool sendData(float ppm, float voltage, int rawValue) {{
    WiFiClient client;
    HTTPClient http;
    
    String postData = String("deviceId=") + deviceId +
                     "&ppm=" + String(ppm, 2) +
                     "&voltage=" + String(voltage, 2) +
                     "&rawValue=" + String(rawValue);
    
    Serial.println("Sending data to server...");
    Serial.printf("Endpoint: %s\\n", apiEndpoint);
    Serial.printf("Data: %s\\n", postData.c_str());
    
    if (!http.begin(client, apiEndpoint)) {{
        Serial.println("Failed to connect to server");
        return false;
    }}
    
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");
    int httpResponse = http.POST(postData);
    
    bool success = false;
    if (httpResponse > 0) {{
        Serial.printf("HTTP Response: %d\\n", httpResponse);
        Serial.printf("Response: %s\\n", http.getString().c_str());
        success = (httpResponse == HTTP_CODE_OK);
    }} else {{
        Serial.printf("HTTP Error: %s\\n", http.errorToString(httpResponse).c_str());
    }}
    
    http.end();
    return success;
}}'''
    except Exception as e:
        log_error(e, "Arduino code generation")
        raise

def lambda_handler(event, context):
    """Main Lambda handler function"""
    print('Event received:', json.dumps(event, indent=2))
    
    try:
        # Parse input data
        input_data = event
        if isinstance(event, str):
            input_data = json.loads(event)
        
        # Handle nested Payload structure
        if 'Payload' in input_data:
            if isinstance(input_data['Payload'], str):
                input_data = json.loads(input_data['Payload'])
            else:
                input_data = input_data['Payload']
        elif 'body' in input_data:
            if isinstance(input_data['body'], str):
                input_data = json.loads(input_data['body'])
            else:
                input_data = input_data['body']
        
        print('Parsed input data:', json.dumps(input_data))

        # Check if this is a status check request
        if input_data.get('action') == 'status':
            user_id = input_data.get('userId')
            device_id = input_data.get('deviceId')
            
            if not user_id or not device_id:
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': 'Missing userId or deviceId for status check'
                    })
                }

            binary_s3_key = f'{user_id}/compiled-binaries/{device_id}/firmware.bin'
            try:
                # Check if file exists
                s3.head_object(
                    Bucket=os.environ['FIRMWARE_BUCKET'],
                    Key=binary_s3_key
                )
                
                # Generate signed URL
                signed_url = s3.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': os.environ['FIRMWARE_BUCKET'],
                        'Key': binary_s3_key
                    },
                    ExpiresIn=3600
                )
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'status': 'COMPLETED',
                        'firmwareUrl': signed_url,
                        'deviceId': device_id,
                        'userId': user_id
                    })
                }
            except s3.exceptions.ClientError as e:
                if e.response['Error']['Code'] == '404':
                    return {
                        'statusCode': 202,
                        'body': json.dumps({
                            'status': 'PROCESSING',
                            'message': 'Firmware compilation in progress'
                        })
                    }
                raise

        # Extract and validate required fields
        ssid = input_data.get('ssid')
        password = input_data.get('password')
        device_id = input_data.get('deviceId', f'device-{str(uuid.uuid4())[:8]}')
        user_id = input_data.get('userId')

        # For async processing, continue with compilation
        if input_data.get('async_processing'):
            # Rest of your existing compilation logic
            if not all([ssid, password]):
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': 'Missing required fields'
                    })
                }

            # Setup directories
            base_temp_dir = input_data.get('baseTempDir', '/tmp')
            temp_dir = os.path.join(base_temp_dir, device_id)
            temp_arduino_dir = os.path.join(base_temp_dir, 'arduino')
            arduino_data_dir = os.path.join(temp_arduino_dir, 'data')
            compiled_binary_dir = os.path.join(temp_dir, 'compiled')
            arduino_code_dir = os.path.join(temp_dir, device_id)

            # Create directories
            for directory in [
                temp_dir,
                compiled_binary_dir,
                arduino_code_dir,
                arduino_data_dir,
                os.path.join(temp_arduino_dir, 'downloads'),
                os.path.join(temp_arduino_dir, 'user')
            ]:
                os.makedirs(directory, exist_ok=True)

            # Setup Arduino CLI and compile
            arduino_cli_path = download_and_extract_arduino_cli(temp_dir)
            arduino_code = generate_arduino_code(ssid, password, device_id)
            main_sketch_path = os.path.join(arduino_code_dir, f'{device_id}.ino')
            
            with open(main_sketch_path, 'w') as f:
                f.write(arduino_code)

            env = {
                'HOME': temp_dir,
                'ARDUINO_DATA_DIR': arduino_data_dir,
                'ARDUINO_DOWNLOADS_DIR': os.path.join(temp_arduino_dir, 'downloads'),
                'ARDUINO_USER_DIR': os.path.join(temp_arduino_dir, 'user'),
                'ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS': 'https://arduino.esp8266.com/stable/package_esp8266com_index.json'
            }

            config_path = input_data.get('configPath', os.path.join(temp_arduino_dir, 'arduino-cli.yaml'))
            config_content = f'''
board_manager:
  additional_urls:
    - https://arduino.esp8266.com/stable/package_esp8266com_index.json
directories:
  data: {arduino_data_dir}
  downloads: {env['ARDUINO_DOWNLOADS_DIR']}
  user: {env['ARDUINO_USER_DIR']}
logging:
  level: info
library:
  enable_unsafe_install: true
'''
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w') as f:
                f.write(config_content)

            initialize_esp8266_platform(arduino_cli_path, config_path, env)

            print('Compiling Arduino code...')
            run_command(
                f'{arduino_cli_path}/bin/arduino-cli compile --config-file "{config_path}" '
                f'--fqbn esp8266:esp8266:nodemcuv2 "{device_id}.ino" --output-dir "{compiled_binary_dir}"',
                work_dir=arduino_code_dir,
                env=env
            )

            if 'FIRMWARE_BUCKET' not in os.environ:
                raise Exception("FIRMWARE_BUCKET environment variable is not set")

            firmware_bucket = os.environ['FIRMWARE_BUCKET']
            arduino_code_s3_key = f'{user_id}/{device_id}/wifi_config.ino'
            binary_s3_key = f'{user_id}/compiled-binaries/{device_id}/firmware.bin'

            compiled_binaries = [f for f in os.listdir(compiled_binary_dir) if f.endswith('.bin')]
            if not compiled_binaries:
                raise Exception("No compiled binary found in output directory")

            s3.put_object(
                Bucket=firmware_bucket,
                Key=arduino_code_s3_key,
                Body=arduino_code,
                ContentType='text/plain'
            )

            binary_path = os.path.join(compiled_binary_dir, compiled_binaries[0])
            with open(binary_path, 'rb') as f:
                s3.put_object(
                    Bucket=firmware_bucket,
                    Key=binary_s3_key,
                    Body=f.read(),
                    ContentType='application/octet-stream'
                )

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'COMPLETED',
                    'message': 'Compilation successful'
                })
            }

        # For initial request, start async processing and return immediately
        lambda_client.invoke(
            FunctionName=context.function_name,
            InvocationType='Event',
            Payload=json.dumps({
                'ssid': ssid,
                'password': password,
                'deviceId': device_id,
                'userId': user_id,
                'async_processing': True
            })
        )

        return {
            'statusCode': 202,
            'body': json.dumps({
                'status': 'PROCESSING',
                'userId': user_id,
                'deviceId': device_id,
                'message': 'Compilation started'
            })
        }

    except Exception as e:
        log_error(e, "Lambda handler")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'details': traceback.format_exc(),
                'deviceId': device_id if 'device_id' in locals() else None,
                'userId': user_id if 'user_id' in locals() else None
            })
        }