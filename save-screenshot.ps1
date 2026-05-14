# Save clipboard screenshot to a temp file and print the path
# Run this after taking a screenshot with Win+Shift+S

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$path = "$env:TEMP\screenshot_$timestamp.png"

Add-Type -AssemblyName System.Windows.Forms
$clip = [System.Windows.Forms.Clipboard]::GetImage()
if ($clip) {
    $clip.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output "Saved to: $path"
} else {
    Write-Output "No image in clipboard. Take a screenshot first (Win+Shift+S)."
}
