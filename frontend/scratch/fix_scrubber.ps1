$path = 'd:\Major Project\frontend\src\modules\app\components\TimelineScrubber.tsx'
$content = [System.IO.File]::ReadAllText($path)
$newContent = $content.Replace('setPlaybackMode(e.target.value)', 'setPlaybackMode(e.target.value as any)')
[System.IO.File]::WriteAllText($path, $newContent)
