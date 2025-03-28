from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Dict, Any, Union
import os
from dotenv import load_dotenv
import logging
import asyncio
import openai
from datetime import datetime
import json
import uuid
import time
from starlette.websockets import WebSocketDisconnect
from app.config import ROLE_PROMPTS, MODERATOR_CONFIG, AI_CONFIG, PROMPT_TEMPLATES, ROUND_TOPICS, MESSAGE_TYPES

# 載入環境變數
load_dotenv()

# 配置日誌
log_level_str = os.getenv("LOG_LEVEL", "INFO")
log_file = os.getenv("LOG_FILE", "app/logs/app.log")

# 將字符串日誌級別轉換為對應的logging級別
log_level = getattr(logging, log_level_str.upper(), logging.INFO)

# 確保日誌目錄存在
os.makedirs(os.path.dirname(log_file), exist_ok=True)

# 設置日誌配置
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"日誌級別設置為 {log_level_str}，日誌文件路徑為 {log_file}")

# 創建FastAPI應用
app = FastAPI(
    title="FlyPig AI Conference API",
    description="飛豬隊友AI虛擬會議系統的後端API",
    version="1.5.0"
)

# 創建靜態文件目錄
os.makedirs("app/static", exist_ok=True)

# 添加靜態文件支持
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# CORS設定
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置OpenAI
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logger.warning("OPENAI_API_KEY環境變量未設置。LLM功能將不可用。")
else:
    # 新版OpenAI API不再使用全局設置
    # 我們將在每次調用時創建客戶端並提供API密鑰
    logger.info("OpenAI API密鑰已設置")

# 創建OpenAI客戶端實例的函數
def get_openai_client():
    """
    根據 API 金鑰創建 OpenAI 客戶端
    支持新舊版本 SDK 和不同格式的 API 金鑰
    """
    global openai_api_key
    
    if not openai_api_key:
        logger.warning("未設置 OpenAI API 金鑰，無法創建客戶端")
        return None
    
    # 掩蔽 API 金鑰用於日誌
    masked_key = (openai_api_key[:5] + "..." + openai_api_key[-5:]) if len(openai_api_key) > 10 else "***"
    logger.info(f"正在使用 API 金鑰創建客戶端 (已遮蔽: {masked_key})")
    
    try:
        # 首先嘗試使用現代的 OpenAI 客戶端
        try:
            client = openai.OpenAI(api_key=openai_api_key)
            logger.info("成功創建現代 OpenAI 客戶端")
            return client
        except (TypeError, ValueError, ImportError) as e:
            logger.warning(f"使用現代客戶端失敗: {str(e)}，嘗試傳統方式")
            
        # 如果現代客戶端失敗，嘗試使用傳統全局配置
        try:
            # 為傳統方法設置 API 金鑰
            openai.api_key = openai_api_key
            
            # 檢查是否支持舊式 API
            if hasattr(openai, 'ChatCompletion') and callable(getattr(openai.ChatCompletion, 'create', None)):
                # 測試客戶端有效性
                logger.info("使用傳統 OpenAI 客戶端 (全局配置)")
                return openai
            else:
                logger.warning("傳統 API 端點不可用，無法創建客戶端")
                return None
                
        except Exception as legacy_err:
            logger.error(f"傳統方式配置失敗: {str(legacy_err)}")
            return None
            
    except Exception as e:
        logger.error(f"創建 OpenAI 客戶端時發生未預期的錯誤: {str(e)}")
        return None

# 數據模型
class Participant(BaseModel):
    id: str
    name: str
    title: str
    personality: str = ""  # 設為可選，默認為空字符串
    expertise: str = ""    # 設為可選，默認為空字符串
    isActive: bool = True
    
    class Config:
        # 允許額外的字段
        extra = "ignore"
    
class ConferenceConfig(BaseModel):
    topic: str
    participants: List[Participant]
    rounds: int = Field(ge=1, le=20, default=3)
    language: str = "繁體中文"
    conclusion: bool = True
    
    class Config:
        # 允許額外的字段
        extra = "ignore"

class Message(BaseModel):
    id: str
    speakerId: str
    speakerName: str
    speakerTitle: str
    text: str
    timestamp: str

# 內存存儲（在實際生產環境中應使用數據庫）
active_conferences = {}
connected_clients = {}

# API路由
@app.get("/")
def read_root():
    return {"message": "歡迎使用飛豬隊友AI虛擬會議系統API"}

@app.get("/api-test", response_class=HTMLResponse)
async def api_test_page():
    """OpenAI API 測試頁面"""
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-Hant">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>OpenAI API 連接測試</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                line-height: 1.6;
            }
            h1 {
                color: #333;
                text-align: center;
                margin-bottom: 30px;
            }
            .container {
                background-color: #f9f9f9;
                border-radius: 8px;
                padding: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .api-status {
                margin: 20px 0;
                padding: 15px;
                border-radius: 5px;
            }
            .success {
                background-color: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }
            .error {
                background-color: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }
            .warning {
                background-color: #fff3cd;
                color: #856404;
                border: 1px solid #ffeeba;
            }
            input, textarea {
                width: 100%;
                padding: 10px;
                margin: 10px 0;
                border: 1px solid #ddd;
                border-radius: 4px;
                box-sizing: border-box;
            }
            button {
                background-color: #4CAF50;
                color: white;
                padding: 10px 15px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
            }
            button:hover {
                background-color: #45a049;
            }
            button:disabled {
                background-color: #cccccc;
                cursor: not-allowed;
            }
            .response {
                margin-top: 20px;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #f5f5f5;
                white-space: pre-wrap;
                max-height: 300px;
                overflow-y: auto;
            }
            .loading {
                text-align: center;
                margin: 10px 0;
                display: none;
            }
            .spinner {
                border: 4px solid #f3f3f3;
                border-top: 4px solid #3498db;
                border-radius: 50%;
                width: 20px;
                height: 20px;
                animation: spin 2s linear infinite;
                display: inline-block;
                margin-right: 10px;
                vertical-align: middle;
            }
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
        </style>
    </head>
    <body>
        <h1>OpenAI API 連接測試</h1>
        
        <div class="container">
            <h2>API 狀態</h2>
            <div id="apiStatus" class="api-status warning">
                檢查中...
            </div>
            
            <h2>API 金鑰設定</h2>
            <div>
                <label for="apiKey">OpenAI API 金鑰</label>
                <input type="text" id="apiKey" placeholder="輸入你的 API Key (只會暫時使用，不會儲存)" />
                <button id="updateApiKey">更新 API 金鑰</button>
            </div>
            
            <h2>即時對話測試</h2>
            <div>
                <label for="message">輸入訊息</label>
                <textarea id="message" rows="4" placeholder="輸入想問 AI 的內容..."></textarea>
                <button id="sendMessage">發送訊息</button>
                
                <div class="loading" id="loading">
                    <span class="spinner"></span> 正在處理請求...
                </div>
                
                <h3>回應</h3>
                <div class="response" id="response">
                    尚未有對話...
                </div>
            </div>
        </div>
        
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                // 初始檢查 API 狀態
                checkApiStatus();
                
                // 綁定按鈕事件
                document.getElementById('updateApiKey').addEventListener('click', updateApiKey);
                document.getElementById('sendMessage').addEventListener('click', sendMessage);
            });
            
            // 檢查 API 狀態
            async function checkApiStatus() {
                const statusElement = document.getElementById('apiStatus');
                try {
                    const response = await fetch('/api/test');
                    const data = await response.json();
                    
                    if (data.openai && data.openai.connected) {
                        statusElement.className = 'api-status success';
                        statusElement.innerHTML = `<strong>成功!</strong> API 連接正常。<br>回應: ${data.openai.response || ''}`;
                    } else {
                        statusElement.className = 'api-status error';
                        statusElement.innerHTML = `<strong>錯誤!</strong> API 連接失敗。<br>原因: ${data.openai?.reason || '未知錯誤'}`;
                    }
                } catch (error) {
                    statusElement.className = 'api-status error';
                    statusElement.innerHTML = `<strong>錯誤!</strong> 無法檢查 API 狀態。<br>錯誤信息: ${error.message}`;
                }
            }
            
            // 更新 API 金鑰
            async function updateApiKey() {
                const apiKey = document.getElementById('apiKey').value.trim();
                if (!apiKey) {
                    alert('請輸入有效的 API 金鑰');
                    return;
                }
                
                const statusElement = document.getElementById('apiStatus');
                statusElement.className = 'api-status warning';
                statusElement.textContent = '正在更新 API 金鑰...';
                
                try {
                    const response = await fetch('/api/update-api-key', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ api_key: apiKey }),
                    });
                    
                    const data = await response.json();
                    if (data.success) {
                        statusElement.className = 'api-status success';
                        statusElement.textContent = '成功! API 金鑰已更新。正在重新檢查連接...';
                        setTimeout(checkApiStatus, 1000);
                    } else {
                        statusElement.className = 'api-status error';
                        statusElement.textContent = `錯誤! 無法更新 API 金鑰。原因: ${data.error || '未知錯誤'}`;
                    }
                } catch (error) {
                    statusElement.className = 'api-status error';
                    statusElement.textContent = `錯誤! 無法更新 API 金鑰。錯誤信息: ${error.message}`;
                }
            }
            
            // 發送測試訊息
            async function sendMessage() {
                const message = document.getElementById('message').value.trim();
                if (!message) {
                    alert('請輸入訊息');
                    return;
                }
                
                const loadingElement = document.getElementById('loading');
                const responseElement = document.getElementById('response');
                const sendButton = document.getElementById('sendMessage');
                
                loadingElement.style.display = 'block';
                sendButton.disabled = true;
                
                try {
                    const response = await fetch('/api/test/message', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ message: message, topic: '測試對話' }),
                    });
                    
                    const data = await response.json();
                    if (data.success) {
                        responseElement.textContent = data.message || '無回應內容';
                    } else {
                        responseElement.textContent = `錯誤! ${data.error || '未知錯誤'}`;
                    }
                } catch (error) {
                    responseElement.textContent = `錯誤! 無法發送訊息。錯誤信息: ${error.message}`;
                } finally {
                    loadingElement.style.display = 'none';
                    sendButton.disabled = false;
                }
            }
        </script>
    </body>
    </html>
    """
    return html_content

@app.get("/api/test")
async def test_api():
    """測試 API 連接和 OpenAI 配置狀態"""
    try:
        # 準備回應資料
        response_data = {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "api_key_set": bool(openai_api_key),
            "openai": {
                "connected": False,
                "reason": "尚未測試"
            }
        }
        
        # 如果 API 金鑰設置，檢查格式
        if openai_api_key:
            is_new_format = openai_api_key.startswith("sk-proj-")
            masked_key = openai_api_key[:5] + "..." + openai_api_key[-5:] if len(openai_api_key) > 10 else "***"
            response_data["api_key"] = {
                "format": "新格式 (sk-proj-*)" if is_new_format else "標準格式 (sk-*)",
                "masked": masked_key
            }
            logger.info(f"API 測試 - 使用 {response_data['api_key']['format']} 的 API 金鑰")
        
        # 獲取 OpenAI 庫版本
        try:
            response_data["openai_version"] = openai.__version__
        except (AttributeError, ImportError):
            response_data["openai_version"] = "未知"
        
        # 測試 OpenAI 連接
        client = get_openai_client()
        if client:
            try:
                # 簡單測試調用
                logger.info("執行 OpenAI API 連接測試")
                
                if hasattr(client, 'chat') and hasattr(client.chat, 'completions'):
                    # 使用新版 API
                    logger.info("使用新版 API 格式進行測試調用")
                    response = client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "簡短的測試回應"}],
                        max_tokens=5
                    )
                    resp_text = response.choices[0].message.content.strip()
                    response_data["openai"]["api_type"] = "新版 OpenAI 客戶端"
                else:
                    # 使用舊版 API
                    logger.info("使用舊版 API 格式進行測試調用")
                    response = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "簡短的測試回應"}],
                        max_tokens=5
                    )
                    if hasattr(response.choices[0], 'message'):
                        resp_text = response.choices[0].message.content.strip()
                    else:
                        resp_text = response.choices[0]['message']['content'].strip()
                    response_data["openai"]["api_type"] = "傳統 OpenAI API"
                
                response_data["openai"] = {
                    "connected": True,
                    "response": resp_text,
                    "model": "gpt-3.5-turbo",
                    "api_type": response_data["openai"].get("api_type", "未知"),
                    "timestamp": datetime.now().isoformat()
                }
                logger.info(f"OpenAI API 測試成功: {resp_text}")
                
            except Exception as api_err:
                logger.error(f"OpenAI API 調用測試失敗: {str(api_err)}")
                
                # 獲取詳細錯誤信息
                error_details = {}
                try:
                    if hasattr(api_err, 'json'):
                        error_details = api_err.json()
                    elif hasattr(api_err, 'response') and hasattr(api_err.response, 'json'):
                        error_details = api_err.response.json()
                except:
                    pass
                
                response_data["openai"] = {
                    "connected": False, 
                    "reason": str(api_err)[:200],
                    "error_type": type(api_err).__name__,
                    "details": error_details,
                    "timestamp": datetime.now().isoformat()
                }
        else:
            logger.warning("API 金鑰未設置或客戶端創建失敗")
            response_data["openai"] = {
                "connected": False, 
                "reason": "API 金鑰未設置或客戶端創建失敗",
                "timestamp": datetime.now().isoformat()
            }
            
    except Exception as e:
        logger.error(f"OpenAI API 測試失敗: {str(e)}")
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "api_key_set": bool(openai_api_key),
            "error": str(e)[:200],
            "error_type": type(e).__name__
        }
    
    return response_data

# 測試消息請求數據模型
class TestMessageRequest(BaseModel):
    message: str
    topic: str = "測試主題"

class ApiKeyUpdateRequest(BaseModel):
    api_key: str

@app.post("/api/update-api-key")
async def update_api_key(request: ApiKeyUpdateRequest):
    """臨時更新 OpenAI API 金鑰（僅用於當前會話）"""
    try:
        # 記錄請求（不記錄完整 API Key）
        masked_key = request.api_key[:5] + "..." + request.api_key[-5:] if len(request.api_key) > 10 else "***"
        logger.info(f"收到更新 API 金鑰請求 (已遮蔽: {masked_key})")
        
        # 檢查金鑰格式
        is_new_format = request.api_key.startswith("sk-proj-")
        logger.info(f"API 金鑰格式檢查: {'新格式 (sk-proj-*)' if is_new_format else '標準格式 (sk-*)'}")
        
        # 更新全局變量中的 API Key
        global openai_api_key
        old_key = openai_api_key
        openai_api_key = request.api_key
        
        # 嘗試創建 OpenAI 客戶端以測試金鑰
        client = get_openai_client()
        if not client:
            # 如果創建失敗，恢復舊金鑰
            logger.error("無法使用新 API 金鑰創建客戶端，恢復原金鑰")
            openai_api_key = old_key
            return {
                "success": False,
                "error": "無法使用提供的 API 金鑰創建 OpenAI 客戶端",
                "details": "客戶端創建失敗",
                "api_format": "新格式 (sk-proj-*)" if is_new_format else "標準格式 (sk-*)"
            }
            
        # 嘗試簡單調用以確認金鑰有效
        logger.info("測試新 API 金鑰與 OpenAI 服務的連線")
        try:
            # 檢查客戶端類型並使用適當的 API 調用
            if hasattr(client, 'chat') and hasattr(client.chat, 'completions'):
                # 使用新版 API
                logger.info("使用新版 API 格式進行測試調用")
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": "API 金鑰測試"}],
                    max_tokens=5
                )
                resp_text = response.choices[0].message.content.strip()
            else:
                # 使用舊版 API
                logger.info("使用舊版 API 格式進行測試調用")
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": "API 金鑰測試"}],
                    max_tokens=5
                )
                if hasattr(response.choices[0], 'message'):
                    resp_text = response.choices[0].message.content.strip()
                else:
                    resp_text = response.choices[0]['message']['content'].strip()
            
            logger.info(f"API 金鑰測試成功，回應: {resp_text}")
            return {
                "success": True,
                "message": "API 金鑰已更新並通過測試",
                "response": resp_text,
                "api_format": "新格式 (sk-proj-*)" if is_new_format else "標準格式 (sk-*)",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as api_err:
            # 如果 API 調用失敗，嘗試獲取詳細錯誤信息
            error_details = {}
            try:
                if hasattr(api_err, 'json'):
                    error_details = api_err.json()
                elif hasattr(api_err, 'response') and hasattr(api_err.response, 'json'):
                    error_details = api_err.response.json()
            except:
                error_details = {"error": str(api_err)[:200]}
            
            # 恢復舊金鑰
            logger.error(f"API 金鑰測試失敗: {str(api_err)}")
            openai_api_key = old_key
            
            return {
                "success": False,
                "error": f"API 金鑰無效或不兼容: {str(api_err)[:200]}",
                "error_type": type(api_err).__name__,
                "details": error_details,
                "api_format": "新格式 (sk-proj-*)" if is_new_format else "標準格式 (sk-*)",
                "timestamp": datetime.now().isoformat()
            }
                
    except Exception as e:
        logger.error(f"更新 API 金鑰時出錯: {str(e)}")
        return {
            "success": False,
            "error": f"更新 API 金鑰處理失敗: {str(e)[:200]}",
            "error_type": type(e).__name__,
            "timestamp": datetime.now().isoformat()
        }

@app.post("/api/test/message")
async def test_message(request: TestMessageRequest):
    """處理測試頁面發送的消息請求"""
    try:
        # 記錄請求
        logger.info(f"收到測試消息請求: {request.json()}")
        
        # 獲取OpenAI客戶端
        client = get_openai_client()
        if not client:
            logger.warning("未設置OpenAI API密鑰或客戶端創建失敗，無法處理請求")
            return {
                "success": False,
                "message": "未配置OpenAI API或客戶端創建失敗，無法處理請求",
                "timestamp": datetime.now().isoformat()
            }
        
        # 構建提示詞
        system_message = f"你是會議助手，正在參與一個關於「{request.topic}」的討論。請用繁體中文回應，風格專業但友善。"
        user_message = request.message
        
        logger.info(f"準備發送到OpenAI的提示詞: {system_message}")
        logger.info(f"使用者訊息: {user_message}")
        
        # 檢查API金鑰格式
        is_new_format = openai_api_key.startswith("sk-proj-") if openai_api_key else False
        logger.info(f"使用的 API 金鑰格式: {'sk-proj-* (新格式)' if is_new_format else '標準格式'}")
        
        # 調用OpenAI API
        try:
            if hasattr(client, 'chat') and hasattr(client.chat, 'completions'):
                # 嘗試新版 API
                logger.info("使用新版 API 格式發送請求")
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content.strip()
            else:
                # 嘗試舊版 API
                logger.info("使用舊版 API 格式發送請求")
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                if hasattr(response.choices[0], 'message'):
                    ai_response = response.choices[0].message.content.strip()
                else:
                    ai_response = response.choices[0]['message']['content'].strip()
            
            logger.info(f"OpenAI API 回應: {ai_response}")
            
            return {
                "success": True,
                "message": ai_response,
                "timestamp": datetime.now().isoformat(),
                "topic": request.topic
            }
            
        except Exception as api_err:
            logger.error(f"OpenAI API 調用失敗: {str(api_err)}")
            
            # 獲取更詳細的錯誤信息
            error_details = {}
            try:
                if hasattr(api_err, 'json'):
                    error_details = api_err.json()
                elif hasattr(api_err, 'response') and hasattr(api_err.response, 'json'):
                    error_details = api_err.response.json()
            except:
                pass
            
            return {
                "success": False,
                "error": f"OpenAI API 調用失敗: {str(api_err)[:200]}",
                "error_type": type(api_err).__name__,
                "details": error_details,
                "timestamp": datetime.now().isoformat()
            }
            
    except Exception as e:
        logger.error(f"處理測試消息請求失敗: {str(e)}")
        return {
            "success": False,
            "error": f"處理請求失敗: {str(e)[:200]}",
            "timestamp": datetime.now().isoformat()
        }

@app.post("/api/conference/start")
async def start_conference(config: ConferenceConfig, background_tasks: BackgroundTasks):
    try:
        # 記錄請求資料內容
        logger.info(f"接收到會議配置請求: {config.json()}")
        
        # 生成會議ID
        conference_id = str(uuid.uuid4())
        
        # 驗證配置
        active_participants = [p for p in config.participants if p.isActive]
        logger.info(f"活躍參與者數量: {len(active_participants)}")
        
        if len(active_participants) < 2:
            raise HTTPException(status_code=400, detail="至少需要2位參與者才能開始會議")
        
        if not config.topic:
            raise HTTPException(status_code=400, detail="會議主題不能為空")
        
        # 儲存會議配置
        active_conferences[conference_id] = {
            "id": conference_id,
            "config": config.dict(),
            "messages": [],
            "stage": "waiting",
            "current_round": 0,
            "start_time": datetime.now().isoformat()
        }
        
        # 記錄儲存成功
        logger.info(f"成功創建會議 {conference_id}")
        
        # 在背景執行會議流程
        background_tasks.add_task(run_conference, conference_id)
        
        return {
            "success": True, 
            "conferenceId": conference_id,
            "message": "會議已開始初始化"
        }
    except ValidationError as ve:
        # 記錄 Pydantic 驗證錯誤
        logger.error(f"請求資料驗證失敗: {str(ve)}")
        return {
            "success": False,
            "conferenceId": "",
            "error": f"請求資料格式不正確: {str(ve)}"
        }
    except Exception as e:
        logger.error(f"啟動會議失敗: {str(e)}")
        return {
            "success": False,
            "conferenceId": "",
            "error": f"啟動會議失敗: {str(e)}"
        }

@app.get("/api/conference/{conference_id}")
def get_conference(conference_id: str):
    if conference_id not in active_conferences:
        raise HTTPException(status_code=404, detail="找不到指定的會議")
    
    return active_conferences[conference_id]

@app.get("/api/conference/{conference_id}/messages")
def get_conference_messages(conference_id: str, limit: int = 50, offset: int = 0):
    if conference_id not in active_conferences:
        raise HTTPException(status_code=404, detail="找不到指定的會議")
    
    messages = active_conferences[conference_id]["messages"]
    return {
        "total": len(messages),
        "messages": messages[offset:offset+limit]
    }

# 會議執行邏輯
async def run_conference(conference_id: str):
    """執行會議的主要邏輯"""
    conf = active_conferences[conference_id]
    config = conf["config"]
    
    # 更新狀態為介紹階段
    await update_conference_stage(conference_id, "introduction")
    
    # 生成並發送自我介紹
    await generate_introductions(conference_id)
    
    # 進入討論階段
    await update_conference_stage(conference_id, "discussion")
    
    # 進行多輪討論
    for round_num in range(1, config["rounds"] + 1):
        await run_discussion_round(conference_id, round_num)
    
    # 生成結論
    await update_conference_stage(conference_id, "conclusion")
    await generate_conclusion(conference_id)
    
    # 標記會議結束
    await update_conference_stage(conference_id, "ended")
    
    logger.info(f"Conference {conference_id} completed")

async def update_conference_stage(conference_id: str, stage: str):
    """更新會議階段並通知客戶端"""
    conf = active_conferences[conference_id]
    conf["stage"] = stage
    
    # 通過WebSocket通知客戶端
    await broadcast_message(conference_id, {
        "type": MESSAGE_TYPES["stage_change"],
        "stage": stage
    })
    logger.info(f"Conference {conference_id} stage changed to {stage}")

async def update_current_round(conference_id: str, round_num: int):
    """更新當前回合並通知客戶端"""
    conf = active_conferences[conference_id]
    conf["current_round"] = round_num
    
    # 通過WebSocket通知客戶端
    await broadcast_message(conference_id, {
        "type": MESSAGE_TYPES["round_update"],
        "round": round_num
    })

# MVP階段使用模擬的回應，實際環境中使用OpenAI API
async def generate_ai_response(prompt: str, participant_id: str, temperature: float = 0.7) -> str:
    """使用OpenAI生成回應"""
    try:
        # 檢查API密鑰是否已配置
        client = get_openai_client()
        if not client:
            logger.warning("未設置OpenAI API密鑰，使用預設回應")
            return f"這是一個預設回應，因為未配置OpenAI API。我是{participant_id}。"
        
        # 構建角色提示詞
        role_prompt = ROLE_PROMPTS.get(participant_id, "")
        
        try:
            # 嘗試使用新版API格式
            if hasattr(client, 'chat') and hasattr(client.chat, 'completions'):
                response = client.chat.completions.create(
                    model=AI_CONFIG["default_model"],
                    messages=[
                        {"role": "system", "content": AI_CONFIG["system_message_template"].format(participant_id=participant_id, role_prompt=role_prompt)},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    max_tokens=AI_CONFIG["max_tokens"]
                )
                return response.choices[0].message.content.strip()
            else:
                # 使用舊版API格式
                response = client.ChatCompletion.create(
                    model=AI_CONFIG["default_model"],
                    messages=[
                        {"role": "system", "content": AI_CONFIG["system_message_template"].format(participant_id=participant_id, role_prompt=role_prompt)},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    max_tokens=AI_CONFIG["max_tokens"]
                )
                return response.choices[0].message.content.strip()
        except AttributeError as attr_err:
            # 處理可能的API結構差異
            logger.warning(f"嘗試調用OpenAI API時發生屬性錯誤: {str(attr_err)}")
            response = openai.ChatCompletion.create(
                model=AI_CONFIG["default_model"],
                messages=[
                    {"role": "system", "content": AI_CONFIG["system_message_template"].format(participant_id=participant_id, role_prompt=role_prompt)},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=AI_CONFIG["max_tokens"]
            )
            
            if hasattr(response.choices[0], 'message'):
                return response.choices[0].message.content.strip()
            return response.choices[0]['message']['content'].strip()
            
    except Exception as e:
        logger.error(f"OpenAI API調用失敗: {str(e)}")
        return f"生成回應時發生錯誤。我是{participant_id}，我會繼續參與討論。錯誤訊息: {str(e)[:100]}"

async def generate_introductions(conference_id: str):
    """生成所有參與者的自我介紹"""
    conf = active_conferences[conference_id]
    config = conf["config"]
    topic = config["topic"]
    
    # 豬秘書(作為主持人)介紹會議
    await add_message(
        conference_id,
        MODERATOR_CONFIG["id"],
        f"大家好，我是{MODERATOR_CONFIG['name']}，擔任今天會議的秘書。歡迎參加主題為「{topic}」的會議。現在我們將進行自我介紹，請各位簡單介紹自己並談談對今天主題的看法。自我介紹完成後，我們將由主席引導進入正式討論階段。"
    )
    
    # 等待1秒使界面顯示更自然
    await asyncio.sleep(1)
    
    # 參與者依次進行自我介紹
    for participant in config["participants"]:
        if not participant["isActive"]:
            continue
        
        # 跳過主持人(豬秘書)，因為已經在開場白中介紹過自己
        if participant["id"] == MODERATOR_CONFIG["id"]:
            continue
            
        # 構建一般參與者的提示
        prompt = PROMPT_TEMPLATES["introduction"].format(
            name=participant['name'],
            title=participant['title'],
            topic=topic
        )
        
        # 生成回應
        response = await generate_ai_response(prompt, participant["id"], participant.get("temperature", 0.7))
        
        # 添加消息並廣播
        await add_message(conference_id, participant["id"], response)
        
        # 模擬打字延遲
        await asyncio.sleep(3)
    
    # 注意：此處不再添加主持人的結束語，將直接由主席在第一輪討論中開場

async def run_discussion_round(conference_id: str, round_num: int):
    """執行一個討論回合"""
    conf = active_conferences[conference_id]
    config = conf["config"]
    topic = config["topic"]
    
    # 更新當前回合
    await update_current_round(conference_id, round_num)
    
    # 設置主席（如果有指定）或使用默認主席
    chair = None
    if "chair" in config and config["chair"]:
        chair = next((p for p in config["participants"] if p["id"] == config["chair"]), None)
    
    if not chair:
        # 使用默認主席（選擇第一個非主持人的活躍參與者）
        chair = next((p for p in config["participants"] 
                     if p["isActive"] and p["id"] != MODERATOR_CONFIG["id"]), None)
        
        if not chair:
            # 如果沒有其他適合的參與者，則使用一個默認設置
            chair = {
                "id": "default_chair",
                "name": "默認主席",
                "title": "會議引導者",
                "temperature": 0.7
            }
    
    # 生成回合主題
    round_topic = get_round_topic(round_num, topic)
    
    # 取得之前的消息作為上下文
    previous_messages = []
    if round_num > 1:
        # 獲取最多10條之前的消息作為上下文
        previous_messages = [m["text"] for m in conf["messages"][-min(10, len(conf["messages"])):]]
    context = "\n".join(previous_messages)
    
    # 主席開場白
    chair_prompt = PROMPT_TEMPLATES["chair_opening"].format(
        name=chair['name'],
        title=chair['title'],
        round_num=round_num,
        topic=topic,
        round_topic=round_topic,
        context=context if round_num > 1 else ""
    )
    
    chair_response = await generate_ai_response(chair_prompt, chair["id"], chair.get("temperature", 0.7))
    
    chair_message = {
        "id": f"{conference_id}_round{round_num}_chair",
        "speakerId": chair["id"],
        "speakerName": chair["name"],
        "speakerTitle": chair["title"],
        "text": chair_response,
        "timestamp": datetime.now().isoformat()
    }
    
    # 儲存消息
    conf["messages"].append(chair_message)
    
    # 通過WebSocket發送消息
    await broadcast_message(conference_id, {
        "type": MESSAGE_TYPES["new_message"],
        "message": chair_message,
        "current_speaker": chair["id"]
    })
    
    # 模擬打字延遲
    await asyncio.sleep(3)
    
    # 獲取所有活躍參與者，排除主席和豬秘書(主持人)
    chair_id = chair["id"]
    active_participants = [p for p in config["participants"] 
                         if p["isActive"] 
                         and p["id"] != chair_id
                         and p["id"] != MODERATOR_CONFIG["id"]]
    
    for idx, participant in enumerate(active_participants):
        # 收集之前的消息作為上下文
        previous_messages = [m["text"] for m in conf["messages"][-min(5, len(conf["messages"])):]]
        context = "\n".join(previous_messages)
        
        # 強調對之前發言的回應
        modified_prompt = PROMPT_TEMPLATES["discussion"].format(
            name=participant['name'],
            title=participant['title'],
            topic=topic,
            round_topic=round_topic,
            context=context
        )
        # 添加額外指示，確保發言的連貫性和相關性
        modified_prompt += "\n請確保你的發言與之前的討論相關，特別是回應最近的1-2位發言者的觀點。避免泛泛而談，要有針對性地展開討論。"
        
        # 生成回應
        response = await generate_ai_response(modified_prompt, participant["id"], participant.get("temperature", 0.7))
        
        # 建立消息物件
        message = {
            "id": f"{conference_id}_round{round_num}_{participant['id']}",
            "speakerId": participant["id"],
            "speakerName": participant["name"],
            "speakerTitle": participant["title"],
            "text": response,
            "timestamp": datetime.now().isoformat()
        }
        
        # 儲存消息
        conf["messages"].append(message)
        
        # 通過WebSocket發送消息
        await broadcast_message(conference_id, {
            "type": MESSAGE_TYPES["new_message"],
            "message": message,
            "current_speaker": participant["id"]
        })
        
        # 模擬打字延遲
        await asyncio.sleep(4)

    await broadcast_message(conference_id, {
        "type": MESSAGE_TYPES["round_completed"],
        "round": round_num
    })

async def generate_conclusion(conference_id: str):
    """生成會議結論"""
    conf = active_conferences[conference_id]
    config = conf["config"]
    topic = config["topic"]
    
    # 獲取主席
    chair = None
    if "chair" in config and config["chair"]:
        chair = next((p for p in config["participants"] if p["id"] == config["chair"]), None)
    
    if not chair:
        # 使用第一個活躍非秘書參與者作為主席
        chair = next((p for p in config["participants"] 
                    if p["isActive"] and p["id"] != MODERATOR_CONFIG["id"]), None)
    
    # 主席引導結論階段
    if chair:
        await add_message(
            conference_id,
            chair["id"],
            f"感謝各位的精彩討論。我們已經完成了所有討論回合，現在進入會議的總結階段。讓我們請{MODERATOR_CONFIG['name']}為我們整理今天會議的重點內容。"
        )
    else:
        # 如果沒有主席，則由豬秘書自己引導
        await add_message(
            conference_id,
            MODERATOR_CONFIG["id"],
            f"感謝各位的精彩討論。我們已經完成了所有討論回合，現在進入會議的總結階段。作為會議秘書，我將為大家總結今天的會議要點。"
        )
    
    await asyncio.sleep(2)
    
    # 收集所有消息作為上下文
    all_messages = [f"{m.get('speakerName', 'Unknown')} ({m.get('speakerTitle', 'Unknown')}): {m.get('text', '')}" for m in conf.get("messages", [])]
    context = "\n".join(all_messages[-30:])  # 最後30條消息，增加上下文範圍
    
    # 構建特殊的秘書結論提示
    secretary_prompt = """
    你是{name}（{title}），負責整理會議記錄並提出總結。
    
    會議主題是「{topic}」，經過了多輪討論。
    
    以下是會議中的發言摘要：
    {context}
    
    請你用繁體中文進行以下工作：
    1. 簡短回應主席，表示你將進行會議總結
    2. 總結整場會議的討論重點和主要觀點
    3. 條理清晰地列出5-7點關鍵結論或行動項目
    4. 提出1-2個後續可能需要關注的方向
    
    格式為：先有一段對主席的回應，然後是總結內容，最後是帶編號的結論列表。總字數控制在400字以內。
    """.format(
        name=MODERATOR_CONFIG['name'],
        title=MODERATOR_CONFIG['title'],
        topic=topic,
        context=context
    )
    
    try:
        # 生成總結
        client = get_openai_client()
        
        if not client:
            # 如果API客戶端不可用，返回一個通用結論
            conclusion = f"謝謝主席。作為會議秘書，我整理了關於「{topic}」的討論要點。由於技術原因，無法生成完整的分析，但仍感謝各位的積極參與和寶貴意見。"
        else:
            try:
                # 修改API調用，不使用await
                response = client.chat.completions.create(
                    model=AI_CONFIG["default_model"],
                    temperature=0.5,  # 使用較低的溫度確保結論更加連貫和精確
                    messages=[
                        {"role": "system", "content": f"你是會議秘書{MODERATOR_CONFIG['name']}。你的工作是整理和總結會議內容，提供清晰的結論和後續行動項目。"},
                        {"role": "user", "content": secretary_prompt}
                    ],
                    max_tokens=800
                )
                
                conclusion = response.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"生成結論時發生錯誤: {str(e)}")
                conclusion = f"謝謝主席。作為會議秘書，我想總結一下今天關於「{topic}」的討論，但在生成過程中遇到了一些技術問題。根據我記錄的內容，我們討論了這個主題的多個方面，並達成了一些共識。感謝各位的參與和寶貴意見。"
        
        # 添加豬秘書的總結消息
        await add_message(
            conference_id,
            MODERATOR_CONFIG["id"],
            conclusion
        )
        
        await asyncio.sleep(3)
        
        # 主席結束會議
        if chair:
            await add_message(
                conference_id,
                chair["id"],
                f"感謝{MODERATOR_CONFIG['name']}的精彩總結，也感謝各位的積極參與。今天的會議到此結束，祝大家工作順利！"
            )
        else:
            # 如果沒有主席，豬秘書自己結束會議
            await add_message(
                conference_id,
                MODERATOR_CONFIG["id"],
                f"以上就是今天會議的總結。感謝各位的積極參與。今天的會議到此結束，祝大家工作順利！"
            )
        
    except Exception as e:
        logger.error(f"生成結論過程中發生錯誤: {str(e)}")
        await add_message(
            conference_id,
            MODERATOR_CONFIG["id"],
            f"感謝各位的參與。由於技術原因，我無法生成完整的會議總結。今天關於「{topic}」的會議到此結束，謝謝大家！"
        )

def get_round_topic(round_num: int, main_topic: str) -> str:
    """獲取每輪討論的具體主題"""
    if round_num in ROUND_TOPICS:
        return ROUND_TOPICS[round_num].format(topic=main_topic)
    return f"{main_topic}的進一步討論要點"

# 原生WebSocket端點保持不變
@app.websocket("/ws/conference/{conference_id}")
async def websocket_endpoint(websocket: WebSocket, conference_id: str):
    await websocket.accept()
    
    client_info = f"{websocket.client.host}:{websocket.client.port}"
    logger.info(f"WebSocket連接已建立 - 客戶端: {client_info}，會議ID: {conference_id}")
    
    if conference_id not in active_conferences:
        logger.warning(f"客戶端嘗試連接不存在的會議 {conference_id}")
        await websocket.send_json({
            "type": MESSAGE_TYPES["error"],
            "message": "會議不存在"
        })
        await websocket.close()
        return
    
    if conference_id not in connected_clients:
        connected_clients[conference_id] = []
    
    connected_clients[conference_id].append(websocket)
    logger.info(f"客戶端已連接到會議 {conference_id}, 當前連接數: {len(connected_clients[conference_id])}")
    
    # 發送現有消息和狀態
    conference = active_conferences[conference_id]
    init_data = {
        "type": MESSAGE_TYPES["init"],
        "messages": conference.get("messages", []),
        "stage": conference.get("stage", "waiting"),
        "current_round": conference.get("current_round", 0),
        "conclusion": conference.get("conclusion")
    }
    logger.info(f"向客戶端發送初始化數據 - 會議ID: {conference_id}, 階段: {conference.get('stage', 'waiting')}")
    await websocket.send_json(init_data)
    
    # 如果是第一個客戶端連接，開始自我介紹
    if len(connected_clients[conference_id]) == 1 and conference["stage"] == "waiting":
        logger.info(f"首位客戶端已連接，開始會議 {conference_id} 的自我介紹階段")
        await process_introductions(conference_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"收到來自客戶端的消息: {data}")
            await process_client_message(conference_id, data)
    except WebSocketDisconnect:
        connected_clients[conference_id].remove(websocket)
        logger.info(f"客戶端已斷開連接，會議 {conference_id}，剩餘連接: {len(connected_clients[conference_id])}")
    except Exception as e:
        logger.error(f"WebSocket連接錯誤: {str(e)}")
        try:
            connected_clients[conference_id].remove(websocket)
        except ValueError:
            pass
        logger.exception("WebSocket處理過程中發生異常")

async def process_client_message(conference_id: str, data: str):
    """處理從客戶端收到的消息"""
    try:
        message = json.loads(data)
        message_type = message.get("type", "")
        logger.info(f"處理客戶端消息，類型: {message_type}")
        
        if message_type == "next_round":
            await process_next_round(conference_id)
        elif message_type == "end_conference":
            await end_conference(conference_id)
    except json.JSONDecodeError:
        logger.error(f"無法解析客戶端消息: {data}")
    except Exception as e:
        logger.error(f"處理客戶端消息時出錯: {str(e)}")

async def broadcast_message(conference_id: str, message: dict):
    """向會議中的所有客戶端廣播消息"""
    if conference_id not in connected_clients:
        logger.warning(f"嘗試向不存在的會議 {conference_id} 廣播消息")
        return
    
    clients_count = len(connected_clients[conference_id])
    if clients_count == 0:
        logger.warning(f"會議 {conference_id} 沒有連接的客戶端，無法廣播消息")
        return
        
    logger.info(f"正在向會議 {conference_id} 的 {clients_count} 個客戶端廣播消息，類型: {message.get('type', 'unknown')}")
    
    success_count = 0
    for client in connected_clients[conference_id]:
        try:
            await client.send_json(message)
            success_count += 1
        except Exception as e:
            logger.error(f"向客戶端廣播消息失敗: {str(e)}")
    
    logger.info(f"廣播完成 - 成功: {success_count}/{clients_count}")

async def add_message(conference_id: str, speaker_id: str, text: str):
    """添加消息並廣播給所有客戶端"""
    conference = active_conferences.get(conference_id)
    if not conference:
        logger.error(f"嘗試添加消息到不存在的會議: {conference_id}")
        return
    
    participant = None
    for p in conference["config"]["participants"]:
        if p["id"] == speaker_id:
            participant = p
            break
    
    if not participant:
        if speaker_id == "moderator":
            participant = {
                "id": MODERATOR_CONFIG["id"],
                "name": MODERATOR_CONFIG["name"],
                "title": MODERATOR_CONFIG["title"]
            }
        else:
            logger.error(f"找不到ID為 {speaker_id} 的參與者")
            return
    
    message = {
        "id": str(uuid.uuid4()),
        "speakerId": speaker_id,
        "speakerName": participant.get("name", "未知"),
        "speakerTitle": participant.get("title", "未知"),
        "text": text,
        "timestamp": datetime.now().isoformat()
    }
    
    if "messages" not in conference:
        conference["messages"] = []
    
    conference["messages"].append(message)
    
    await broadcast_message(conference_id, {
        "type": MESSAGE_TYPES["new_message"],
        "message": message,
        "current_speaker": speaker_id
    })
    
    # 添加短暫延遲，使對話更自然
    await asyncio.sleep(0.5)

async def process_next_round(conference_id: str):
    """進入下一輪討論"""
    conference = active_conferences.get(conference_id)
    if not conference or conference["stage"] != "discussion":
        return
    
    current_round = conference.get("current_round", 0)
    total_rounds = conference["config"].get("rounds", 3)
    
    if current_round < total_rounds:
        await update_current_round(conference_id, current_round + 1)
        await run_discussion_round(conference_id, current_round + 1)
    else:
        # 已經是最後一輪
        await process_conclusion(conference_id)

async def end_conference(conference_id: str):
    """結束會議"""
    if conference_id not in active_conferences:
        return
    
    # 處理會議結束
    conference = active_conferences[conference_id]
    
    if conference["stage"] not in ["conclusion", "ended"]:
        await process_conclusion(conference_id)
    
    # 清理資源
    # 我們保留會議數據一段時間供查詢，但可以清理不必要的連接
    if conference_id in connected_clients:
        for client in connected_clients[conference_id]:
            try:
                await client.close()
            except:
                pass
        connected_clients[conference_id] = []

async def process_introductions(conference_id: str):
    """處理會議的自我介紹階段"""
    logger.info(f"開始處理會議 {conference_id} 的自我介紹階段")
    
    if conference_id not in active_conferences:
        logger.error(f"嘗試處理不存在的會議: {conference_id}")
        return
    
    conference = active_conferences[conference_id]
    
    # 更新會議階段為「介紹」
    await update_conference_stage(conference_id, "introduction")
    
    # 生成並發送自我介紹
    await generate_introductions(conference_id)
    
    # 進入討論階段
    await update_conference_stage(conference_id, "discussion")
    
    # 開始第一輪討論
    await run_discussion_round(conference_id, 1)

async def process_conclusion(conference_id: str):
    """處理會議的結論階段"""
    logger.info(f"開始處理會議 {conference_id} 的結論階段")
    
    if conference_id not in active_conferences:
        logger.error(f"嘗試處理不存在的會議: {conference_id}")
        return
    
    # 更新會議階段為「結論」
    await update_conference_stage(conference_id, "conclusion")
    
    # 生成會議結論
    await generate_conclusion(conference_id)
    
    # 標記會議結束
    await update_conference_stage(conference_id, "ended")
    
    logger.info(f"會議 {conference_id} 已完成")

# 添加全局異常處理器
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # 記錄詳細的請求驗證錯誤
    error_detail = str(exc)
    logger.error(f"請求資料驗證失敗: {error_detail}")
    logger.error(f"請求路徑: {request.url.path}")
    
    try:
        body = await request.json()
        logger.error(f"請求體: {json.dumps(body, ensure_ascii=False)}")
    except Exception as e:
        logger.error(f"無法解析請求體: {str(e)}")
    
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "請求資料格式不正確，請檢查會議設定",
            "detail": [{"loc": err["loc"], "msg": err["msg"]} for err in exc.errors()]
        },
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 