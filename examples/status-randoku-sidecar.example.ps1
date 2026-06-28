<#
.SYNOPSIS
    Example status script for randoku-sidecar and its MCP tunnel.
.DESCRIPTION
    Reports the local listener, the tunnel URL, and the MCP tool surface.
    Safe to adapt for your own machine.
#>

$ListenHost = '127.0.0.1'
$ListenPort = 4750
$TunnelUrl = 'https://your-domain.example/mcp'
$McpUrl = "http://127.0.0.1:$ListenPort/mcp"

Write-Host "Local MCP URL : $McpUrl"
Write-Host "Tunnel URL    : $TunnelUrl"
Write-Host "Port listening: $ListenHost`:$ListenPort"
Write-Host 'Probe the MCP endpoint with an MCP client, not a browser GET.'
