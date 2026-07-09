param(
    [string]$BaseUrl = "",
    [string]$ConversationID = "",
    [string]$TaskPrefix = "task",
    [int]$StartTaskNumber = 1,
    [string]$UserId = "",
    [string]$AppId = "",
    [string]$AppSecret = "",
    [switch]$ShowSign,
    [switch]$Help,
    [string]$AccountId = "929E5E2CD8F91D9B-A0B923820DCC509A-4930383"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function T {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TextBase64
    )
    return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($TextBase64))
}

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

function Show-Usage {
    @(
        (T "6ZqQ5byP5Lit5pat5Lqk5LqS5rWL6K+V6ISa5pys"),
        "",
        (T "5ZCv5Yqo5pa55byP77ya"),
        "  powershell -ExecutionPolicy Bypass -File .\test_implicit_interrupt.ps1 -BaseUrl http://127.0.0.1:8001",
        "",
        (T "5Lqk5LqS5ZG95Luk77ya"),
        (T "ICAvaGVscCAgICAgICAgICAgICDmmL7npLrluK7liqk="),
        (T "ICAvaGlzdG9yeSAgICAgICAgICDmiZPljbDlvZPliY3mnKzlnLAgQ2hhdEhpc3Rvcmllcw=="),
        (T "ICAvY2xlYXIgICAgICAgICAgICDmuIXnqbrlvZPliY3mnKzlnLAgQ2hhdEhpc3Rvcmllcw=="),
        (T "ICAvbmV3ICAgICAgICAgICAgICDmlrDlvIDpmo/mnLrkvJror50="),
        (T "ICAvbmV3IGdpZnQwMDEgICAgICDliIfmjaLliLDmjIflrprkvJror53lubbmuIXnqbrmnKzlnLAgQ2hhdEhpc3Rvcmllcw=="),
        (T "ICAvZXhpdCAgICAgICAgICAgICDpgIDlh7o="),
        "",
        (T "5q+P6L2u6K+35rGC6L+U5Zue5ZCO5Lya6K+i6Zeu77ya"),
        (T "ICBhICAgICAgICAgICAgICAgICDkv53lrZjvvIzmqKHmi5/liY3nq6/lsZXnpLrlubbkv53lrZjmnKzova7liLAgQ2hhdEhpc3Rvcmllcw=="),
        (T "ICBpICAgICAgICAgICAgICAgICDkuK3mlq0v5Lii5byD77yM5qih5ouf55So5oi35Lit5pat77yM5pys6L2u5LiN6L+b5YWlIENoYXRIaXN0b3JpZXM="),
        (T "ICBoICAgICAgICAgICAgICAgICDmn6XnnIvlvZPliY0gQ2hhdEhpc3Rvcmllcw=="),
        (T "ICByICAgICAgICAgICAgICAgICDmn6XnnIvmnKzova7ljp/lp4vlk43lupQgSlNPTg=="),
        (T "ICBxICAgICAgICAgICAgICAgICDpgIDlh7o="),
        "",
        (T "5YWz6ZSu6KeC5a+f54K577ya"),
        (T "ICAtIOmAieaLqSBhIOWQju+8jOS4i+S4gOi9ruWPkemAgeeahCBDaGF0SGlzdG9yaWVzIOS8muWMheWQq+S4iuS4gOi9ruOAgg=="),
        (T "ICAtIOmAieaLqSBpIOWQju+8jOS4i+S4gOi9ruWPkemAgeeahCBDaGF0SGlzdG9yaWVzIOS4jeWMheWQq+S4iuS4gOi9ru+8m+acjeWKoeerr+W6lOaOqOaWreS4iuS4gOi9ruiiq+S4reaWreW5tuWbnua7mueKtuaAgeOAgg==")
    ) -join [Environment]::NewLine
}

function New-CdfConversationId {
    return "gift$(Get-Date -Format 'yyyyMMddHHmmss')"
}

function New-CdfTaskId {
    param(
        [string]$Prefix,
        [int]$Number
    )
    return ("{0}{1:D3}" -f $Prefix, $Number)
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

function ConvertTo-CompactJson {
    param(
        [Parameter(Mandatory = $true)]
        $Value,
        [int]$Depth = 50
    )
    return ConvertTo-CdfCompactJson -Value $Value
}

function ConvertTo-PrettyJson {
    param(
        [Parameter(Mandatory = $true)]
        $Value,
        [int]$Depth = 50
    )
    return ConvertTo-Json -InputObject $Value -Depth $Depth
}

function Get-ObjectValue {
    param(
        $Object,
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

function New-CdfSignature {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.IDictionary]$Body,
        [Parameter(Mandatory = $true)]
        [string]$AppId,
        [Parameter(Mandatory = $true)]
        [string]$AppSecret,
        [Parameter(Mandatory = $true)]
        [string]$Nonce,
        [Parameter(Mandatory = $true)]
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
            $signDict[$key] = ConvertTo-CompactJson -Value $value
        }
        elseif ($value -is [System.Collections.IEnumerable] -and -not ($value -is [string])) {
            $signDict[$key] = ConvertTo-CompactJson -Value @($value)
        }
        else {
            $signDict[$key] = "$value"
        }
    }

    $sortedKeys = [string[]]$signDict.Keys
    [Array]::Sort($sortedKeys, [System.StringComparer]::Ordinal)
    $message = (($sortedKeys | ForEach-Object { "$_=$($signDict[$_])" }) -join "&")

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
        Message = $message
        Signature = (($hashBytes | ForEach-Object { $_.ToString("x2") }) -join "")
    }
}

function Get-ChatHistoriesJson {
    param(
        [array]$ChatHistories
    )
    if ($null -eq $ChatHistories -or $ChatHistories.Count -eq 0) {
        return "[]"
    }
    return ConvertTo-PrettyJson -Value @($ChatHistories)
}

function Get-ResponseAnswerText {
    param(
        $Response
    )

    $blocks = @()
    $data = Get-ObjectValue -Object $Response -Name "data" -DefaultValue @()
    foreach ($block in @($data)) {
        $content = [string](Get-ObjectValue -Object $block -Name "content" -DefaultValue "")
        if (-not [string]::IsNullOrWhiteSpace($content)) {
            $blocks += $content
        }
    }
    return ($blocks -join "`n")
}

function Get-AnswerJsonBlocks {
    param(
        [string]$AnswerText
    )

    $objects = @()
    $matches = [regex]::Matches($AnswerText, '(?s)```(?:json)?\s*(.*?)\s*```')

    foreach ($match in $matches) {
        $jsonText = $match.Groups[1].Value.Trim()
        if ([string]::IsNullOrWhiteSpace($jsonText)) {
            continue
        }

        try {
            $objects += ($jsonText | ConvertFrom-Json)
        }
        catch {
            # Ignore non-JSON fenced blocks.
        }
    }

    return @($objects)
}

function Write-RecommendationExtract {
    param(
        [string]$AnswerText
    )

    $jsonObjects = Get-AnswerJsonBlocks -AnswerText $AnswerText
    $recommendBlock = $null
    $questionBlock = $null

    foreach ($obj in @($jsonObjects)) {
        $type = [string](Get-ObjectValue -Object $obj -Name "type" -DefaultValue "")

        if ($type -eq "pro-recommend") {
            $recommendBlock = $obj
        }
        elseif ($type -eq "add-questions" -or $type -eq "add_question") {
            $questionBlock = $obj
        }
    }

    if ($null -eq $recommendBlock) {
        return
    }

    Write-Host ""
    Write-Host ("=== {0} ===" -f (T "5o6o6I2Q5ZWG5ZOB5o+Q5Y+W")) -ForegroundColor Green

    $products = @(Get-ObjectValue -Object $recommendBlock -Name "data" -DefaultValue @())
    if ($products.Count -gt 0) {
        Write-Host ("{0}:" -f (T "5o6o6I2Q5ZWG5ZOB"))

        $index = 1
        foreach ($product in $products) {
            $name = [string](Get-ObjectValue -Object $product -Name "productName" -DefaultValue "")
            $price = [string](Get-ObjectValue -Object $product -Name "payPrice" -DefaultValue "")
            $id = [string](Get-ObjectValue -Object $product -Name "productId" -DefaultValue "")
            $pic = [string](Get-ObjectValue -Object $product -Name "productPic" -DefaultValue "")

            Write-Host ("  {0}. {1} | {2}: {3} | ID: {4}" -f $index, $name, (T "5Lu35qC8"), $price, $id)
            if (-not [string]::IsNullOrWhiteSpace($pic)) {
                Write-Host ("     {0}: {1}" -f (T "5Zu+54mH"), $pic)
            }

            $index++
        }
    }

    if ($null -ne $questionBlock) {
        $questions = @(Get-ObjectValue -Object $questionBlock -Name "data" -DefaultValue @())
        if ($questions.Count -gt 0) {
            Write-Host "add_question:"

            $index = 1
            foreach ($question in $questions) {
                $title = [string](Get-ObjectValue -Object $question -Name "title" -DefaultValue "")
                if (-not [string]::IsNullOrWhiteSpace($title)) {
                    Write-Host ("  {0}. {1}" -f $index, $title)
                    $index++
                }
            }
        }
    }
}

function Write-ResponseSummary {
    param(
        $Response
    )

    Write-Host ""
    Write-Host "=== $(T "5pyN5Yqh56uv6L+U5Zue") ===" -ForegroundColor Cyan
    Write-Host ("{0}: {1}" -f (T "5Lu75YqhSUQodGFza0lkKQ=="), (Get-ObjectValue -Object $Response -Name "taskId" -DefaultValue ""))
    Write-Host ("{0}: {1}" -f (T "6YCB56S85oSP5Zu+KGlzR2lmdEludGVudGlvbik="), (Get-ObjectValue -Object $Response -Name "isGiftIntention" -DefaultValue ""))
    Write-Host ("{0}: {1}" -f (T "5piv5ZCm5Lit5patKGlzSW50ZXJydXB0ZWQp"), (Get-ObjectValue -Object $Response -Name "isInterrupted" -DefaultValue ""))

    $answerText = Get-ResponseAnswerText -Response $Response
    if ([string]::IsNullOrWhiteSpace($answerText)) {
        Write-Host (T "77yI5peg5paH5pys5YaF5a6577yJ")
    }
    else {
        Write-RecommendationExtract -AnswerText $answerText

        Write-Host ""
        Write-Host $answerText
    }
}

function Invoke-ImplicitChat {
    param(
        [string]$BaseUrl,
        [string]$ConversationID,
        [string]$TaskId,
        [string]$QueryText,
        [array]$ChatHistories,
        [string]$UserId,
        [string]$AppId,
        [string]$AppSecret,
        [switch]$ShowSign,
        [string]$AccountId
    )

    $queryJson = ConvertTo-CompactJson -Value ([ordered]@{ 
        queryText = $QueryText 
        accountId = $AccountId

    })
    $body = [ordered]@{
        ConversationID = $ConversationID
        taskId = $TaskId
        Query = $queryJson
        ChatHistories = @($ChatHistories)
        UserID = $UserId
        IsInterrupt = $false
    }

    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $nonce = [guid]::NewGuid().ToString("N")
    $sign = New-CdfSignature -Body $body -AppId $AppId -AppSecret $AppSecret -Nonce $nonce -Timestamp $timestamp

    if ($ShowSign) {
        Write-Host "=== $(T "562+5ZCN5a2X56ym5Liy") ===" -ForegroundColor DarkCyan
        Write-Host $sign.Message
        Write-Host "=== $(T "562+5ZCN") ===" -ForegroundColor DarkCyan
        Write-Host $sign.Signature
    }

    $bodyJson = ConvertTo-CompactJson -Value $body
    $requestUrl = "$($BaseUrl.TrimEnd('/'))/cdfai/v1/fudan/chat"

    $response = Invoke-WebRequest `
        -Method Post `
        -Uri $requestUrl `
        -UseBasicParsing `
        -Headers @{
            appid = $AppId
            timestamp = "$timestamp"
            nonce = $nonce
            signature = $sign.Signature
        } `
        -ContentType "application/json; charset=utf-8" `
        -Body $bodyJson

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

    return [pscustomobject]@{
        QueryJson = $queryJson
        BodyJson = $bodyJson
        ResponseJson = $responseText
        Response = ($responseText | ConvertFrom-Json)
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = Get-CdfConfigValue -EnvName "CDF_BASE_URL" -DefaultValue "http://127.0.0.1:8001"
}
if ([string]::IsNullOrWhiteSpace($UserId)) {
    $UserId = Get-CdfConfigValue -EnvName "CDF_USER_ID" -DefaultValue "u100"
}
if ([string]::IsNullOrWhiteSpace($AppId)) {
    $AppId = Get-CdfConfigValue -EnvName "CDF_APP_ID" -DefaultValue "cdf_26283b073aa0433a"
}
if ([string]::IsNullOrWhiteSpace($AppSecret)) {
    $AppSecret = Get-CdfConfigValue -EnvName "CDF_APP_SECRET" -DefaultValue "6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4"
}
if ([string]::IsNullOrWhiteSpace($ConversationID)) {
    $ConversationID = New-CdfConversationId
}
if ([string]::IsNullOrWhiteSpace($AccountId)) {
    $AccountId = Get-CdfConfigValue -EnvName "CDF_ACCOUNT_ID" -DefaultValue "你的默认accountId"
}

$chatHistories = @()
$taskNumber = [Math]::Max($StartTaskNumber, 1)

Write-Host (T "5bey6L+b5YWl6ZqQ5byP5Lit5pat5Lqk5LqS5rWL6K+V44CC") -ForegroundColor Cyan
Write-Host "$(T "5pyN5Yqh5Zyw5Z2AKEJhc2VVcmwp"): $BaseUrl" -ForegroundColor Cyan
Write-Host "$(T "5Lya6K+dSUQoQ29udmVyc2F0aW9uSUQp"): $ConversationID" -ForegroundColor Cyan
Write-Host (T "6L6T5YWlIC9oZWxwIOafpeeci+WRveS7pOOAgg==") -ForegroundColor DarkGray

while ($true) {
    $taskId = New-CdfTaskId -Prefix $TaskPrefix -Number $taskNumber
    $inputText = Read-Host "[$ConversationID][$taskId] $(T "5L2g")"
    if ($null -eq $inputText) {
        continue
    }

    $inputText = $inputText.Trim()
    if ([string]::IsNullOrWhiteSpace($inputText)) {
        continue
    }

    if ($inputText -match '^/(exit|quit)$') {
        Write-Host (T "5bey6YCA5Ye644CC") -ForegroundColor Cyan
        break
    }
    if ($inputText -eq "/help") {
        Show-Usage
        continue
    }
    if ($inputText -eq "/history") {
        Write-Host "=== $(T "5b2T5YmN5pys5ZywIENoYXRIaXN0b3JpZXM=") ($($chatHistories.Count)) ===" -ForegroundColor Cyan
        Write-Host (Get-ChatHistoriesJson -ChatHistories $chatHistories)
        continue
    }
    if ($inputText -eq "/clear") {
        $chatHistories = @()
        Write-Host (T "5bey5riF56m65pys5ZywIENoYXRIaXN0b3JpZXPjgII=") -ForegroundColor Yellow
        continue
    }
    if ($inputText -match '^/new(?:\s+(.+))?$') {
        $newId = $Matches[1]
        if ([string]::IsNullOrWhiteSpace($newId)) {
            $ConversationID = New-CdfConversationId
        }
        else {
            $ConversationID = $newId.Trim()
        }
        $chatHistories = @()
        $taskNumber = 1
        Write-Host "$(T "5bey5YiH5o2i5Lya6K+dOg==") $ConversationID$(T "77yb5pys5ZywIENoYXRIaXN0b3JpZXMg5bey5riF56m644CC")" -ForegroundColor Cyan
        continue
    }

    Write-Host ""
    Write-Host "=== $(T "5pys6L2u5Y+R6YCB5YmNIENoYXRIaXN0b3JpZXM=") ($($chatHistories.Count)) ===" -ForegroundColor Yellow
    Write-Host (Get-ChatHistoriesJson -ChatHistories $chatHistories)

    try {
        $result = Invoke-ImplicitChat `
            -BaseUrl $BaseUrl `
            -ConversationID $ConversationID `
            -TaskId $taskId `
            -QueryText $inputText `
            -ChatHistories $chatHistories `
            -UserId $UserId `
            -AppId $AppId `
            -AppSecret $AppSecret `
            -ShowSign:$ShowSign `
            -AccountId $AccountId
    }
    catch {
        Write-Host "$(T "6K+35rGC5aSx6LSlOg==") $($_.Exception.Message)" -ForegroundColor Red
        if ($null -ne $_.Exception.Response) {
            $errorStream = $_.Exception.Response.GetResponseStream()
            if ($null -ne $errorStream) {
                $reader = New-Object System.IO.StreamReader($errorStream, [System.Text.Encoding]::UTF8)
                try {
                    $errorText = $reader.ReadToEnd()
                }
                finally {
                    $reader.Close()
                }
                if (-not [string]::IsNullOrWhiteSpace($errorText)) {
                    Write-Host "$(T "6ZSZ6K+v5ZON5bqUOg==") $errorText" -ForegroundColor DarkRed
                }
            }
        }
        continue
    }

    Write-ResponseSummary -Response $result.Response

    while ($true) {
        $choice = (Read-Host (T "5pys6L2u5aaC5L2V5aSE55CG77yfYT3kv53lrZjliLAgQ2hhdEhpc3Rvcmllc++8jGk95Lit5patL+S4ouW8g++8jGg955yL5Y6G5Y+y77yMcj3ljp/lp4vlk43lupTvvIxxPemAgOWHug==")).Trim().ToLowerInvariant()
        if ($choice -eq "a") {
            $answerText = Get-ResponseAnswerText -Response $result.Response
            $chatHistories += [ordered]@{
                query = $result.QueryJson
                answer = $answerText
            }
            Write-Host (T "5bey5L+d5a2Y5pys6L2u5Yiw5pys5ZywIENoYXRIaXN0b3JpZXPjgILkuIvkuIDova7mnI3liqHnq6/lupTop4bkuLrkuIrkuIDova7mraPluLjlrozmiJDjgII=") -ForegroundColor Green
            Write-Host "=== $(T "5pu05paw5ZCOIENoYXRIaXN0b3JpZXM=") ($($chatHistories.Count)) ===" -ForegroundColor Cyan
            Write-Host (Get-ChatHistoriesJson -ChatHistories $chatHistories)
            $taskNumber++
            break
        }
        elseif ($choice -eq "i") {
            Write-Host (T "5bey5Lii5byD5pys6L2u77yM5LiN5YaZ5YWl5pys5ZywIENoYXRIaXN0b3JpZXPjgILkuIvkuIDova7kvJrnlKjnm7jlkIzljoblj7Llj5HpgIHvvIzmnI3liqHnq6/lupTmjqjmlq3mnKzova7ooqvkuK3mlq3jgII=") -ForegroundColor Yellow
            Write-Host "=== $(T "5L+d5oyB5LiN5Y+Y55qEIENoYXRIaXN0b3JpZXM=") ($($chatHistories.Count)) ===" -ForegroundColor Cyan
            Write-Host (Get-ChatHistoriesJson -ChatHistories $chatHistories)
            $taskNumber++
            break
        }
        elseif ($choice -eq "h") {
            Write-Host "=== $(T "5b2T5YmN5pys5ZywIENoYXRIaXN0b3JpZXM=") ($($chatHistories.Count)) ===" -ForegroundColor Cyan
            Write-Host (Get-ChatHistoriesJson -ChatHistories $chatHistories)
        }
        elseif ($choice -eq "r") {
            Write-Host "=== $(T "5Y6f5aeL5ZON5bqUIEpTT04=") ===" -ForegroundColor Cyan
            Write-Host $result.ResponseJson
        }
        elseif ($choice -eq "q") {
            Write-Host (T "5bey6YCA5Ye644CC") -ForegroundColor Cyan
            exit 0
        }
        else {
            Write-Host (T "6K+36L6T5YWlIGEgLyBpIC8gaCAvIHIgLyBx44CC") -ForegroundColor DarkYellow
        }
    }
}
