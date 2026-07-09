param(
    [Parameter(Position = 0)]
    [ValidateSet("usage", "interactive", "chat", "interrupt")]
    [string]$Mode = "usage",

    [string]$ConversationID = "",

    [string]$TaskId = "",

    [string]$QueryText = "",

    [string]$ChatHistoriesJson = "[]",

    [string]$TaskPrefix = "task",

    [int]$StartTaskNumber = 1
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Get-CdfConfigValue {
    param(
        [string]$EnvName,
        [string]$DefaultValue
    )

    $envValue = [Environment]::GetEnvironmentVariable($EnvName)
    if ([string]::IsNullOrWhiteSpace($envValue)) {
        return $DefaultValue
    }
    return $envValue
}

function New-CdfConversationId {
    return "gift$(Get-Date -Format 'yyyyMMddHHmmss')"
}

function New-CdfTaskId {
    param(
        [string]$TaskPrefix = "task",
        [int]$TaskNumber = 1
    )

    return ("{0}{1:D3}" -f $TaskPrefix, $TaskNumber)
}

function Get-CdfObjectValue {
    param(
        [Parameter(Mandatory = $true)]
        $Object,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        $DefaultValue = $null
    )

    if ($null -eq $Object) {
        return $DefaultValue
    }

    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) {
            return $Object[$Name]
        }
        return $DefaultValue
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $DefaultValue
    }

    return $property.Value
}

function ConvertTo-CdfChatHistories {
    param(
        [string]$ChatHistoriesJson = "[]"
    )

    if ([string]::IsNullOrWhiteSpace($ChatHistoriesJson)) {
        return @()
    }

    $parsed = ConvertFrom-Json -InputObject $ChatHistoriesJson
    if ($null -eq $parsed) {
        return @()
    }

    if ($parsed -is [System.Array]) {
        return $parsed
    }

    return @($parsed)
}

function ConvertTo-CdfJsonString {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append([char]34)

    foreach ($ch in [char[]]$Value) {
        $code = [int]$ch
        if ($code -eq 34) {
            [void]$builder.Append('\"')
        }
        elseif ($code -eq 92) {
            [void]$builder.Append('\\')
        }
        elseif ($code -eq 8) {
            [void]$builder.Append('\b')
        }
        elseif ($code -eq 12) {
            [void]$builder.Append('\f')
        }
        elseif ($code -eq 10) {
            [void]$builder.Append('\n')
        }
        elseif ($code -eq 13) {
            [void]$builder.Append('\r')
        }
        elseif ($code -eq 9) {
            [void]$builder.Append('\t')
        }
        elseif ($code -lt 32) {
            [void]$builder.Append(('\u{0:x4}' -f $code))
        }
        else {
            [void]$builder.Append($ch)
        }
    }

    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function ConvertTo-CdfCompactJson {
    param(
        $Value
    )

    if ($null -eq $Value) {
        return "null"
    }
    if ($Value -is [string]) {
        return ConvertTo-CdfJsonString -Value $Value
    }
    if ($Value -is [bool]) {
        if ($Value) {
            return "true"
        }
        return "false"
    }
    if (
        $Value -is [byte] -or
        $Value -is [sbyte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int] -or
        $Value -is [uint32] -or
        $Value -is [long] -or
        $Value -is [uint64] -or
        $Value -is [single] -or
        $Value -is [double] -or
        $Value -is [decimal]
    ) {
        return [System.Convert]::ToString($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $parts = @()
        foreach ($key in $Value.Keys) {
            $parts += ("{0}:{1}" -f (ConvertTo-CdfJsonString -Value ([string]$key)), (ConvertTo-CdfCompactJson -Value $Value[$key]))
        }
        return "{0}{1}{2}" -f "{", ($parts -join ","), "}"
    }
    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        $parts = @()
        foreach ($item in $Value) {
            $parts += ConvertTo-CdfCompactJson -Value $item
        }
        return "{0}{1}{2}" -f "[", ($parts -join ","), "]"
    }

    $properties = @($Value.PSObject.Properties | Where-Object { $_.MemberType -eq "NoteProperty" -or $_.MemberType -eq "Property" })
    if ($properties.Count -gt 0) {
        $parts = @()
        foreach ($property in $properties) {
            $parts += ("{0}:{1}" -f (ConvertTo-CdfJsonString -Value $property.Name), (ConvertTo-CdfCompactJson -Value $property.Value))
        }
        return "{0}{1}{2}" -f "{", ($parts -join ","), "}"
    }

    return ConvertTo-CdfJsonString -Value ([string]$Value)
}

function New-CdfSignature {
    param(
        [hashtable]$Body,
        [string]$AppId,
        [string]$AppSecret,
        [string]$Nonce,
        [string]$Timestamp
    )

    $signDict = [ordered]@{
        appid = $AppId
        timestamp = "$Timestamp"
        nonce = $Nonce
    }

    foreach ($key in $Body.Keys) {
        $value = $Body[$key]
        if ($null -eq $value) {
            continue
        }

        if ($value -is [string]) {
            $signDict[$key] = $value
        }
        elseif ($value -is [System.Collections.IDictionary] -or $value -is [hashtable]) {
            $signDict[$key] = ConvertTo-CdfCompactJson -Value $value
        }
        elseif ($value -is [System.Collections.IEnumerable] -and -not ($value -is [string])) {
            $signDict[$key] = ConvertTo-CdfCompactJson -Value @($value)
        }
        else {
            $signDict[$key] = "$value"
        }
    }

    $sortedKeys = [string[]]$signDict.Keys
    [Array]::Sort($sortedKeys, [System.StringComparer]::Ordinal)

    $message = (($sortedKeys | ForEach-Object {
        "$_=$($signDict[$_])"
    }) -join "&")

    $hmac = [System.Security.Cryptography.HMACSHA256]::new(
        [System.Text.Encoding]::UTF8.GetBytes($AppSecret)
    )
    try {
        $hashBytes = $hmac.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($message))
    }
    finally {
        $hmac.Dispose()
    }

    return [pscustomobject]@{
        Message   = $message
        Signature = (($hashBytes | ForEach-Object { $_.ToString("x2") }) -join "")
    }
}

function Invoke-CdfRequest {
    param(
        [hashtable]$Body,
        [string]$BaseUrl,
        [string]$AppId,
        [string]$AppSecret,
        [switch]$IsInterrupt
    )

    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $nonce = [guid]::NewGuid().ToString("N")

    $signResult = New-CdfSignature `
        -Body $Body `
        -AppId $AppId `
        -AppSecret $AppSecret `
        -Nonce $nonce `
        -Timestamp $timestamp

    if ($IsInterrupt) {
        Write-Host "=== Client Interrupt Sign Message ===" -ForegroundColor Cyan
    }
    else {
        Write-Host "=== Client Sign Message ===" -ForegroundColor Cyan
    }
    Write-Host $signResult.Message

    if ($IsInterrupt) {
        Write-Host "=== Client Interrupt Signature ===" -ForegroundColor Cyan
    }
    else {
        Write-Host "=== Client Signature ===" -ForegroundColor Cyan
    }
    Write-Host $signResult.Signature

    $bodyJson = ConvertTo-CdfCompactJson -Value $Body
    $requestUrl = "$($BaseUrl.TrimEnd('/'))/cdfai/v1/fudan/chat"

    try {
        $response = Invoke-WebRequest `
            -Method Post `
            -Uri $requestUrl `
            -UseBasicParsing `
            -Headers @{
                appid = $AppId
                timestamp = "$timestamp"
                nonce = $nonce
                signature = $signResult.Signature
            } `
            -ContentType "application/json; charset=utf-8" `
            -Body $bodyJson
    }
    catch {
        if ($_.Exception.Response) {
            $errorStream = $_.Exception.Response.GetResponseStream()
            if ($null -ne $errorStream) {
                $reader = New-Object System.IO.StreamReader($errorStream, [System.Text.Encoding]::UTF8)
                try {
                    $errorText = $reader.ReadToEnd()
                }
                finally {
                    $reader.Close()
                }
                Write-Host $errorText -ForegroundColor Red
            }
        }
        throw
    }

    $stream = $response.RawContentStream
    if ($stream.CanSeek) {
        $stream.Position = 0
    }

    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
    try {
        $responseText = $reader.ReadToEnd()
    }
    finally {
        $reader.Close()
    }

    return $responseText | ConvertFrom-Json
}

function Invoke-CdfChat {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConversationID,

        [Parameter(Mandatory = $true)]
        [string]$TaskId,

        [Parameter(Mandatory = $true)]
        [string]$QueryText,

        [string]$ChatHistoriesJson = "[]",

        [string]$BaseUrl = (Get-CdfConfigValue -EnvName "CDF_BASE_URL" -DefaultValue "http://127.0.0.1:8000"),

        [string]$AppId = (Get-CdfConfigValue -EnvName "CDF_APP_ID" -DefaultValue "cdf_26283b073aa0433a"),

        [string]$AppSecret = (Get-CdfConfigValue -EnvName "CDF_APP_SECRET" -DefaultValue "6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4"),

        [string]$UserId = (Get-CdfConfigValue -EnvName "CDF_USER_ID" -DefaultValue "u100")
    )

    $chatHistories = ConvertTo-CdfChatHistories -ChatHistoriesJson $ChatHistoriesJson

    $body = @{
        ConversationID = $ConversationID
        taskId = $TaskId
        Query = ConvertTo-CdfCompactJson -Value ([ordered]@{ queryText = $QueryText })
        ChatHistories = @($chatHistories)
        UserID = $UserId
        IsInterrupt = $false
    }

    Invoke-CdfRequest `
        -Body $body `
        -BaseUrl $BaseUrl `
        -AppId $AppId `
        -AppSecret $AppSecret
}

function Invoke-CdfInterrupt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConversationID,

        [Parameter(Mandatory = $true)]
        [string]$TaskId,

        [string]$BaseUrl = (Get-CdfConfigValue -EnvName "CDF_BASE_URL" -DefaultValue "http://127.0.0.1:8000"),

        [string]$AppId = (Get-CdfConfigValue -EnvName "CDF_APP_ID" -DefaultValue "cdf_26283b073aa0433a"),

        [string]$AppSecret = (Get-CdfConfigValue -EnvName "CDF_APP_SECRET" -DefaultValue "6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4"),

        [string]$UserId = (Get-CdfConfigValue -EnvName "CDF_USER_ID" -DefaultValue "u100")
    )

    $body = @{
        ConversationID = $ConversationID
        taskId = $TaskId
        Query = (@{ queryText = "" } | ConvertTo-Json -Compress)
        ChatHistories = @()
        UserID = $UserId
        IsInterrupt = $true
    }

    Invoke-CdfRequest `
        -Body $body `
        -BaseUrl $BaseUrl `
        -AppId $AppId `
        -AppSecret $AppSecret `
        -IsInterrupt
}

function ConvertFrom-CdfJsonBlockContent {
    param(
        $Content
    )

    if ($null -eq $Content) {
        return $null
    }

    $jsonText = ([string]$Content).Trim()
    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        return $null
    }

    if ($jsonText -match '(?s)^```json\s*(.*?)\s*```$') {
        $jsonText = $Matches[1].Trim()
    }
    elseif ($jsonText -match '(?s)^```\s*(.*?)\s*```$') {
        $jsonText = $Matches[1].Trim()
    }

    if (-not ($jsonText.StartsWith("{") -or $jsonText.StartsWith("["))) {
        return $null
    }

    try {
        return $jsonText | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Write-CdfJsonBlockSummary {
    param(
        [Parameter(Mandatory = $true)]
        $JsonObject
    )

    $blockType = [string](Get-CdfObjectValue -Object $JsonObject -Name "type" -DefaultValue "")
    if ($blockType -eq "pro-recommend") {
        $products = Get-CdfObjectValue -Object $JsonObject -Name "data" -DefaultValue @()
        if ($products -isnot [System.Collections.IEnumerable] -or $products -is [string]) {
            $products = @($products)
        }

        Write-Host "助手[商品推荐]>" -ForegroundColor Yellow
        $index = 1
        foreach ($product in @($products)) {
            $name = [string](Get-CdfObjectValue -Object $product -Name "productName" -DefaultValue "")
            $price = [string](Get-CdfObjectValue -Object $product -Name "payPrice" -DefaultValue "")
            $productId = [string](Get-CdfObjectValue -Object $product -Name "productId" -DefaultValue "")
            Write-Host ("  {0}. {1} | ￥{2} | {3}" -f $index, $name, $price, $productId) -ForegroundColor Green
            $index++
        }
        return $true
    }

    if ($blockType -eq "add-questions") {
        $title = [string](Get-CdfObjectValue -Object $JsonObject -Name "title" -DefaultValue "您可能还想问")
        $questions = Get-CdfObjectValue -Object $JsonObject -Name "data" -DefaultValue @()
        if ($questions -isnot [System.Collections.IEnumerable] -or $questions -is [string]) {
            $questions = @($questions)
        }

        Write-Host "助手[$title]>" -ForegroundColor Yellow
        foreach ($question in @($questions)) {
            $questionTitle = [string](Get-CdfObjectValue -Object $question -Name "title" -DefaultValue "")
            if (-not [string]::IsNullOrWhiteSpace($questionTitle)) {
                Write-Host "  - $questionTitle" -ForegroundColor DarkGray
            }
        }
        return $true
    }

    return $false
}

function Write-CdfChatResponse {
    param(
        [Parameter(Mandatory = $true)]
        $Response
    )

    $taskId = [string](Get-CdfObjectValue -Object $Response -Name "taskId" -DefaultValue "")
    if (-not [string]::IsNullOrWhiteSpace($taskId)) {
        Write-Host "taskId: $taskId" -ForegroundColor DarkGray
    }

    $items = Get-CdfObjectValue -Object $Response -Name "data" -DefaultValue $null
    if ($null -eq $items) {
        $items = Get-CdfObjectValue -Object $Response -Name "data_blocks" -DefaultValue @()
    }

    if ($items -isnot [System.Collections.IEnumerable] -or $items -is [string]) {
        $items = @($items)
    }

    $rendered = $false
    foreach ($item in @($items)) {
        if ($null -eq $item) {
            continue
        }

        $type = [string](Get-CdfObjectValue -Object $item -Name "type" -DefaultValue "")
        $content = Get-CdfObjectValue -Object $item -Name "content" -DefaultValue $null

        if ($type -eq "text" -and -not [string]::IsNullOrWhiteSpace([string]$content)) {
            Write-Host "助手> $content" -ForegroundColor Green
            $rendered = $true
            continue
        }

        if ($type -eq "json") {
            $jsonObject = ConvertFrom-CdfJsonBlockContent -Content $content
            if ($null -ne $jsonObject) {
                $handled = Write-CdfJsonBlockSummary -JsonObject $jsonObject
                if ($handled) {
                    $rendered = $true
                    continue
                }
            }
        }

        if (-not [string]::IsNullOrWhiteSpace($type)) {
            Write-Host "助手[$type]>" -ForegroundColor Yellow
        }

        if ($null -ne $content -and -not [string]::IsNullOrWhiteSpace([string]$content)) {
            Write-Host $content
            $rendered = $true
        }
        else {
            $item | ConvertTo-Json -Depth 20
            $rendered = $true
        }
    }

    if (-not $rendered) {
        $Response | ConvertTo-Json -Depth 20
    }
}

function Get-CdfResponseAnswerText {
    param(
        [Parameter(Mandatory = $true)]
        $Response
    )

    $items = Get-CdfObjectValue -Object $Response -Name "data" -DefaultValue $null
    if ($null -eq $items) {
        $items = Get-CdfObjectValue -Object $Response -Name "data_blocks" -DefaultValue @()
    }
    if ($items -isnot [System.Collections.IEnumerable] -or $items -is [string]) {
        $items = @($items)
    }

    $blocks = @()
    foreach ($item in @($items)) {
        $content = [string](Get-CdfObjectValue -Object $item -Name "content" -DefaultValue "")
        if (-not [string]::IsNullOrWhiteSpace($content)) {
            $blocks += $content
        }
    }
    return ($blocks -join "`n")
}

function Show-CdfInteractiveHelp {
    @"
交互命令：
  /help        查看帮助
  /history     查看本地 ChatHistories
  /clear       清空本地 ChatHistories
  /interrupt   发送中断请求
  /new         新开一个会话 ID
  /new conv9   切换到指定会话 ID
  /exit        退出交互模式
"@
}

function Start-CdfChatInteractive {
    param(
        [string]$ConversationID = "",

        [string]$TaskPrefix = "task",

        [int]$StartTaskNumber = 1,

        [string]$BaseUrl = (Get-CdfConfigValue -EnvName "CDF_BASE_URL" -DefaultValue "http://127.0.0.1:8000"),

        [string]$AppId = (Get-CdfConfigValue -EnvName "CDF_APP_ID" -DefaultValue "cdf_26283b073aa0433a"),

        [string]$AppSecret = (Get-CdfConfigValue -EnvName "CDF_APP_SECRET" -DefaultValue "6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4"),

        [string]$UserId = (Get-CdfConfigValue -EnvName "CDF_USER_ID" -DefaultValue "u100")
    )

    if ([string]::IsNullOrWhiteSpace($ConversationID)) {
        $ConversationID = New-CdfConversationId
    }

    $taskNumber = [Math]::Max($StartTaskNumber, 1)
    $chatHistories = @()

    Write-Host "已进入交互测试模式。" -ForegroundColor Cyan
    Write-Host "当前会话: $ConversationID" -ForegroundColor Cyan
    Write-Host (Show-CdfInteractiveHelp) -ForegroundColor DarkGray

    while ($true) {
        $taskId = New-CdfTaskId -TaskPrefix $TaskPrefix -TaskNumber $taskNumber
        $inputText = Read-Host "[$ConversationID][$taskId] 你"

        if ($null -eq $inputText) {
            continue
        }

        $inputText = $inputText.Trim()
        if ([string]::IsNullOrWhiteSpace($inputText)) {
            continue
        }

        if ($inputText -match '^/(exit|quit)$') {
            Write-Host "已退出交互测试。" -ForegroundColor Cyan
            break
        }

        if ($inputText -eq "/help") {
            Write-Host (Show-CdfInteractiveHelp) -ForegroundColor DarkGray
            continue
        }

        if ($inputText -eq "/history") {
            Write-Host "=== 本地 ChatHistories ($($chatHistories.Count)) ===" -ForegroundColor Cyan
            Write-Host (ConvertTo-Json -InputObject @($chatHistories) -Depth 20)
            continue
        }

        if ($inputText -eq "/clear") {
            $chatHistories = @()
            Write-Host "已清空本地 ChatHistories。" -ForegroundColor Yellow
            continue
        }

        if ($inputText -eq "/interrupt") {
            try {
                $interruptResponse = Invoke-CdfInterrupt `
                    -ConversationID $ConversationID `
                    -TaskId $taskId `
                    -BaseUrl $BaseUrl `
                    -AppId $AppId `
                    -AppSecret $AppSecret `
                    -UserId $UserId
                Write-CdfChatResponse -Response $interruptResponse
            }
            catch {
                Write-Host "中断请求失败: $($_.Exception.Message)" -ForegroundColor Red
            }
            $taskNumber++
            continue
        }

        if ($inputText -match '^/new(?:\s+(.+))?$') {
            if ($Matches[1]) {
                $ConversationID = $Matches[1].Trim()
            }
            else {
                $ConversationID = New-CdfConversationId
            }
            $taskNumber = [Math]::Max($StartTaskNumber, 1)
            $chatHistories = @()
            Write-Host "已切换会话: $ConversationID" -ForegroundColor Cyan
            continue
        }

        try {
            Write-Host "=== 本轮发送前 ChatHistories ($($chatHistories.Count)) ===" -ForegroundColor Yellow
            Write-Host (ConvertTo-Json -InputObject @($chatHistories) -Depth 20)

            $response = Invoke-CdfChat `
                -ConversationID $ConversationID `
                -TaskId $taskId `
                -QueryText $inputText `
                -ChatHistoriesJson (ConvertTo-CdfCompactJson -Value @($chatHistories)) `
                -BaseUrl $BaseUrl `
                -AppId $AppId `
                -AppSecret $AppSecret `
                -UserId $UserId
            Write-CdfChatResponse -Response $response

            while ($true) {
                $choice = (Read-Host "本轮如何处理？a=保存到 ChatHistories，i=中断/丢弃，h=看历史，q=退出").Trim().ToLowerInvariant()
                if ($choice -eq "a") {
                    $chatHistories += [ordered]@{
                        query = ConvertTo-CdfCompactJson -Value ([ordered]@{ queryText = $inputText })
                        answer = Get-CdfResponseAnswerText -Response $response
                    }
                    Write-Host "已保存本轮到本地 ChatHistories。下一轮服务端会视为上一轮正常完成。" -ForegroundColor Green
                    break
                }
                elseif ($choice -eq "i") {
                    Write-Host "已丢弃本轮，不写入本地 ChatHistories。下一轮服务端会推断本轮被中断。" -ForegroundColor Yellow
                    break
                }
                elseif ($choice -eq "h") {
                    Write-Host "=== 本地 ChatHistories ($($chatHistories.Count)) ===" -ForegroundColor Cyan
                    Write-Host (ConvertTo-Json -InputObject @($chatHistories) -Depth 20)
                }
                elseif ($choice -eq "q") {
                    Write-Host "已退出交互测试。" -ForegroundColor Cyan
                    return
                }
                else {
                    Write-Host "请输入 a / i / h / q。" -ForegroundColor DarkYellow
                }
            }
        }
        catch {
            Write-Host "请求失败: $($_.Exception.Message)" -ForegroundColor Red
        }

        $taskNumber++
    }
}

function Show-CdfScriptUsage {
    @"
Usage:
  . .\test_chat.ps1
  powershell -ExecutionPolicy Bypass -File .\test_chat.ps1 interactive
  powershell -ExecutionPolicy Bypass -File .\test_chat.ps1 interactive -ConversationID "gift001"
  powershell -ExecutionPolicy Bypass -File .\test_chat.ps1 chat -ConversationID "gift001" -TaskId "t1" -QueryText "开始送礼"
  powershell -ExecutionPolicy Bypass -File .\test_chat.ps1 interrupt -ConversationID "gift001" -TaskId "t2"
  Invoke-CdfChat -ConversationID "gift001" -TaskId "t1" -QueryText "开始送礼" | ConvertTo-Json -Depth 20
  Invoke-CdfChat -ConversationID "gift001" -TaskId "t2" -QueryText "先这样吧，暂时不用推荐了" | ConvertTo-Json -Depth 20
  Invoke-CdfInterrupt -ConversationID "gift001" -TaskId "t3" | ConvertTo-Json -Depth 20

Optional environment variables:
  CDF_BASE_URL
  CDF_APP_ID
  CDF_APP_SECRET
  CDF_USER_ID
"@
}

if ($MyInvocation.InvocationName -ne ".") {
    if ($Mode -eq "interactive") {
        Start-CdfChatInteractive `
            -ConversationID $ConversationID `
            -TaskPrefix $TaskPrefix `
            -StartTaskNumber $StartTaskNumber
    } elseif ($Mode -eq "chat") {
        if ([string]::IsNullOrWhiteSpace($QueryText)) {
            throw "chat 模式必须提供 -QueryText。"
        }
        if ([string]::IsNullOrWhiteSpace($ConversationID)) {
            $ConversationID = New-CdfConversationId
        }
        if ([string]::IsNullOrWhiteSpace($TaskId)) {
            $TaskId = New-CdfTaskId -TaskPrefix $TaskPrefix -TaskNumber $StartTaskNumber
        }
        $chatResponse = Invoke-CdfChat `
            -ConversationID $ConversationID `
            -TaskId $TaskId `
            -QueryText $QueryText `
            -ChatHistoriesJson $ChatHistoriesJson
        $chatResponse | ConvertTo-Json -Depth 20
    } elseif ($Mode -eq "interrupt") {
        if ([string]::IsNullOrWhiteSpace($ConversationID)) {
            throw "interrupt 模式必须提供 -ConversationID。"
        }
        if ([string]::IsNullOrWhiteSpace($TaskId)) {
            $TaskId = New-CdfTaskId -TaskPrefix $TaskPrefix -TaskNumber $StartTaskNumber
        }
        $interruptResponse = Invoke-CdfInterrupt `
            -ConversationID $ConversationID `
            -TaskId $TaskId
        $interruptResponse | ConvertTo-Json -Depth 20
    } else {
        Show-CdfScriptUsage
    }
}
