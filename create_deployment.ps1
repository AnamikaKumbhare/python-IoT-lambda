# create_deployment.ps1

# Ensure we're in the virtual environment
.\venv\Scripts\activate

# Create a new directory for the package
if (Test-Path "package") {
    Remove-Item -Path "package" -Recurse -Force
}
New-Item -ItemType Directory -Path "package"

# Install dependencies into the package directory
pip install -r requirements.txt -t package/

# Create deployment package
if (Test-Path "deployment.zip") {
    Remove-Item -Path "deployment.zip" -Force
}

# Add dependencies to zip
Push-Location package
Compress-Archive -Path * -DestinationPath ..\deployment.zip
Pop-Location

# Add lambda function to zip
Compress-Archive -Path lambda_function.py -Update -DestinationPath deployment.zip

# Clean up
Remove-Item -Path "package" -Recurse -Force

Write-Host "Deployment package created successfully!"
Write-Host "You can find it at: deployment.zip"