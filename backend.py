#!/usr/bin/env python3
"""
Backend API для NFT минта с x402
УЛУЧШЕННАЯ ВЕРСИЯ v2 - с защитой от nonce conflicts и retry логикой
"""

from flask import Flask, request, jsonify, Response, render_template
from web3 import Web3
from dotenv import load_dotenv
import os
import json
import base64
import sys
import traceback
import time
import threading

# Загружаем переменные из .env файла
load_dotenv()

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════
# НАСТРОЙКИ - ЗАПОЛНИ ИХ!
# ═══════════════════════════════════════════════════════════

BASE_RPC = "https://rpc.ankr.com/base/13ca64398a6a0125df8e188d1525542811320f016be33834bba2f4f32be7c4c8"  # Ankr RPC с API ключом
NFT_CONTRACT = os.getenv("NFT_CONTRACT", "0x...")  # Адрес твоего NFT контракта
ADMIN_PRIVATE_KEY = os.getenv("ADMIN_KEY")  # Приватный ключ для минта NFT
MINT_PRICE = int(os.getenv("MINT_PRICE", "1000000"))  # Цена в USDC (1000000 = 1 USDC)
RECIPIENT_ADDRESS = os.getenv("RECIPIENT_ADDRESS")  # Адрес получателя USDC (твой адрес)

w3 = Web3(Web3.HTTPProvider(BASE_RPC))

# Кэш для /api/info (чтобы не тормозить загрузку)
info_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 10  # Кэш на 10 секунд

# Lock для facilitator (предотвращает nonce conflicts)
facilitator_lock = threading.Lock()
mint_lock = threading.Lock()

# Функция для логирования с flush
def log(message):
    print(message)
    sys.stdout.flush()

# ABI для NFT контракта (STUPID402NFT)
NFT_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "txHash", "type": "bytes32"}
        ],
        "name": "mintNFT",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "currentTokenId",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "MAX_SUPPLY",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ═══════════════════════════════════════════════════════════
# X402 ФУНКЦИИ
# ═══════════════════════════════════════════════════════════

def decode_x402_payment(x_payment_header):
    """Декодирует x-payment header из x402"""
    try:
        # Проверка на пустой x-payment
        if not x_payment_header:
            log(f"❌ x-payment пустой")
            return {'valid': False, 'error': 'Empty x-payment'}
        
        # Декодируем base64
        decoded = base64.b64decode(x_payment_header)
        payment_data = json.loads(decoded.decode('utf-8'))
        
        # x402scan использует новую структуру с вложенными полями
        # Проверяем, есть ли payload.authorization (новый формат)
        if 'payload' in payment_data and 'authorization' in payment_data['payload']:
            auth = payment_data['payload']['authorization']
            signature = payment_data['payload'].get('signature')
            
            # Извлекаем данные из authorization
            from_addr = auth.get('from')
            to_addr = auth.get('to')
            value = auth.get('value')
            nonce = auth.get('nonce')
            valid_after = auth.get('validAfter')
            valid_before = auth.get('validBefore')
        else:
            # Старый формат (плоский)
            from_addr = payment_data.get('from')
            to_addr = payment_data.get('to')
            value = payment_data.get('value')
            nonce = payment_data.get('nonce')
            valid_after = payment_data.get('validAfter')
            valid_before = payment_data.get('validBefore')
            signature = payment_data.get('signature')
        
        # Проверяем обязательные поля
        if not all([from_addr, to_addr, value, nonce, valid_after, valid_before, signature]):
            log(f"❌ Отсутствуют обязательные поля")
            return {'valid': False, 'error': 'Missing required fields'}
        
        log(f"✅ Платеж декодирован: from={from_addr}, to={to_addr}, value={value}")
        
        # Генерируем уникальный txHash для этой транзакции
        tx_hash = Web3.keccak(text=f"{from_addr}{nonce}{valid_before}").hex()
        log(f"🔐 Сгенерирован txHash: {tx_hash}")
        
        return {
            'valid': True,
            'from': from_addr,
            'to': to_addr,
            'value': value,
            'nonce': nonce,
            'validAfter': valid_after,
            'validBefore': valid_before,
            'signature': signature,
            'txHash': tx_hash
        }
    except Exception as e:
        log(f"❌ Ошибка декодирования x-payment: {e}")
        log(f"📜 Traceback: {traceback.format_exc()}")
        return {'valid': False, 'error': str(e)}

# ═══════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/api/facilitate', methods=['POST', 'OPTIONS'])
def facilitate():
    """
    Facilitator endpoint - выполняет USDC transfer используя подпись пользователя
    УЛУЧШЕННАЯ ВЕРСИЯ с Lock и retry логикой
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    # Используем Lock чтобы только 1 facilitate за раз
    with facilitator_lock:
        try:
            x_payment = request.headers.get('x-payment')
            if not x_payment and request.is_json:
                try:
                    x_payment = request.json.get('payment')
                except:
                    pass
            
            if not x_payment:
                return jsonify({"error": "Missing x-payment"}), 400
            
            log("🔧 Facilitator: начинаем USDC transfer...")
            
            payment_data = decode_x402_payment(x_payment)
            if not payment_data['valid']:
                return jsonify({"error": "Invalid payment"}), 400
            
            # ABI для USDC transferWithAuthorization
            usdc_abi = [
                {
                    "inputs": [
                        {"name": "from", "type": "address"},
                        {"name": "to", "type": "address"},
                        {"name": "value", "type": "uint256"},
                        {"name": "validAfter", "type": "uint256"},
                        {"name": "validBefore", "type": "uint256"},
                        {"name": "nonce", "type": "bytes32"},
                        {"name": "v", "type": "uint8"},
                        {"name": "r", "type": "bytes32"},
                        {"name": "s", "type": "bytes32"}
                    ],
                    "name": "transferWithAuthorization",
                    "outputs": [],
                    "stateMutability": "nonpayable",
                    "type": "function"
                }
            ]
            
            # Парсим подпись
            signature = payment_data.get('signature')
            if not signature:
                log(f"❌ Отсутствует подпись в платеже")
                return jsonify({"error": "Missing signature"}), 400
            
            if signature.startswith('0x'):
                signature = signature[2:]
            
            sig_bytes = bytes.fromhex(signature)
            r = sig_bytes[:32]  # bytes32
            s = sig_bytes[32:64]  # bytes32
            v = sig_bytes[64]
            
            # Создаем контракт USDC
            usdc_contract = w3.eth.contract(
                address=Web3.to_checksum_address("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
                abi=usdc_abi
            )
            
            # Получаем admin аккаунт
            admin = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
            
            # RETRY логика - 3 попытки
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Получаем PENDING nonce (учитывает ожидающие транзакции)
                    nonce = w3.eth.get_transaction_count(admin.address, 'pending')
                    
                    # Получаем актуальную цену газа
                    base_fee = w3.eth.gas_price
                    
                    # Увеличиваем gas price с каждой попыткой
                    gas_multiplier = 2 + attempt  # 2x, 3x, 4x
                    
                    log(f"🔄 Попытка {attempt + 1}/{max_retries}, nonce={nonce}, gas_multiplier={gas_multiplier}x")
                    
                    # Создаем транзакцию
                    usdc_tx = usdc_contract.functions.transferWithAuthorization(
                        Web3.to_checksum_address(payment_data['from']),
                        Web3.to_checksum_address(payment_data['to']),
                        int(payment_data['value']),
                        int(payment_data['validAfter']),
                        int(payment_data['validBefore']),
                        Web3.to_bytes(hexstr=payment_data['nonce']),
                        v,
                        r,
                        s
                    ).build_transaction({
                        'from': admin.address,
                        'nonce': nonce,
                        'gas': 200000,  # Увеличили с 150k до 200k
                        'maxFeePerGas': base_fee * gas_multiplier,
                        'maxPriorityFeePerGas': w3.to_wei('0.01', 'gwei'),  # Увеличили с 0.001 до 0.01
                        'chainId': 8453
                    })
                    
                    # Подписываем и отправляем
                    signed_usdc = admin.sign_transaction(usdc_tx)
                    usdc_tx_hash = w3.eth.send_raw_transaction(signed_usdc.raw_transaction)
                    
                    log(f"💸 Facilitator: USDC transfer TX: {usdc_tx_hash.hex()}")
                    
                    # Ждем подтверждения
                    receipt = w3.eth.wait_for_transaction_receipt(usdc_tx_hash, timeout=60)
                    
                    if receipt.status == 1:
                        log(f"✅ Facilitator: USDC transfer успешен! Gas used: {receipt.gasUsed}")
                        return jsonify({
                            "success": True,
                            "tx": usdc_tx_hash.hex(),
                            "from": payment_data['from'],
                            "to": payment_data['to'],
                            "value": payment_data['value']
                        })
                    else:
                        log(f"❌ Facilitator: USDC transfer провалился (status=0)")
                        if attempt < max_retries - 1:
                            log(f"⏳ Повторяем через 2 секунды...")
                            time.sleep(2)
                            continue
                        else:
                            return jsonify({"error": "Transfer failed after retries"}), 500
                
                except Exception as tx_error:
                    error_msg = str(tx_error)
                    log(f"⚠️ Попытка {attempt + 1} провалилась: {error_msg}")
                    
                    # Если это nonce error и есть ещё попытки - пробуем снова
                    if 'nonce' in error_msg.lower() or 'replacement' in error_msg.lower():
                        if attempt < max_retries - 1:
                            log(f"⏳ Nonce conflict, повторяем через 3 секунды...")
                            time.sleep(3)
                            continue
                    
                    # Если последняя попытка - возвращаем ошибку
                    if attempt >= max_retries - 1:
                        raise tx_error
            
            return jsonify({"error": "All retries failed"}), 500
                
        except Exception as e:
            log(f"❌ Facilitator error: {str(e)}")
            log(f"📜 Traceback:\n{traceback.format_exc()}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/mint', methods=['GET', 'POST', 'OPTIONS'])
def mint():
    """
    Endpoint для минта NFT
    УЛУЧШЕННАЯ ВЕРСИЯ с Lock
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Получаем x-payment из headers или body
        x_payment = request.headers.get('x-payment')
        if not x_payment and request.is_json:
            try:
                x_payment = request.json.get('payment')
            except:
                pass
        
        log(f"📝 Запрос минта для: {request.headers.get('x-forwarded-for', request.remote_addr)}")
        
        # Если нет x-payment - возвращаем 402
        if not x_payment:
            response = jsonify({
                "error": "Payment required to access this resource",
                "x402Version": 1,
                "facilitator": "https://stupidx402.onrender.com/api/facilitate",
                "accepts": [{
                    "scheme": "exact",
                    "network": "base",
                    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                    "maxAmountRequired": str(MINT_PRICE),
                    "payTo": RECIPIENT_ADDRESS,
                    "resource": "https://stupidx402.onrender.com/api/mint",
                    "description": "Mint 1 STUPID402 NFT.",
                    "mimeType": "application/json",
                    "maxTimeoutSeconds": 300,
                    "outputSchema": {
                        "input": {
                            "type": "http",
                            "method": "GET",
                            "discoverable": True
                        },
                        "output": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "tx": {"type": "string"},
                                "to": {"type": "string"},
                                "tokenId": {"type": "number"}
                            }
                        }
                    },
                    "extra": {
                        "recipientAddress": RECIPIENT_ADDRESS,
                        "name": "USD Coin",
                        "version": "2",
                        "primaryType": "TransferWithAuthorization",
                        "projectName": "STUPID402",
                        "projectDescription": "STUPID402 NFT Collection on Base",
                        "website": "https://stupidx402.onrender.com",
                        "icon": "https://stupidx402.onrender.com/static/icon.png"
                    }
                }]
            })
            response.status_code = 402
            response.headers['Content-Type'] = 'application/json'
            return response
        
        # Проверяем платеж
        log("🔍 Проверяем x402 платеж...")
        payment_data = decode_x402_payment(x_payment)
        
        if not payment_data['valid']:
            return jsonify({
                "x402Version": 1,
                "error": "Invalid payment"
            }), 400
        
        log(f"✅ Платеж валиден! (x402 выполнит перевод USDC на контракт)")
        log(f"✅ Платеж валиден! txHash: {payment_data['txHash']}")
        
        # ВЫПОЛНЯЕМ USDC TRANSFER СРАЗУ
        usdc_transfer_success = False
        usdc_tx_hash = None
        try:
            log("💰 Вызываем facilitator для USDC transfer...")
            import requests as req
            facilitate_response = req.post(
                "https://stupidx402.onrender.com/api/facilitate",
                headers={"x-payment": x_payment},
                json={"payment": x_payment},
                timeout=90  # Увеличили таймаут с 60 до 90 сек
            )
            
            if facilitate_response.status_code == 200:
                result = facilitate_response.json()
                usdc_tx_hash = result.get('tx')
                log(f"✅ USDC transfer выполнен! TX: {usdc_tx_hash}")
                usdc_transfer_success = True
            else:
                log(f"❌ USDC transfer failed: {facilitate_response.text}")
        except Exception as e:
            log(f"⚠️ Ошибка facilitator: {str(e)}")
        
        if not usdc_transfer_success:
            log("❌ Останавливаем минт - USDC не списались")
            return jsonify({
                "x402Version": 1,
                "error": "USDC transfer failed"
            }), 500
        
        # Используем Lock для минта (предотвращает nonce conflicts при минте)
        with mint_lock:
            # Получаем текущий tokenId
            nft_contract = w3.eth.contract(
                address=Web3.to_checksum_address(NFT_CONTRACT),
                abi=NFT_ABI
            )
            
            current_token_id = nft_contract.functions.currentTokenId().call()
            log(f"📊 Текущий tokenId: {current_token_id}")
            
            # Минтим NFT
            user_address = Web3.to_checksum_address(payment_data['from'])
            tx_hash_bytes = Web3.to_bytes(hexstr=payment_data['txHash'])
            
            log(f"🎨 Минтим NFT с payment txHash: {payment_data['txHash']}...")
            
            admin_account = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
            
            # RETRY логика для минта
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Получаем PENDING nonce
                    nonce = w3.eth.get_transaction_count(admin_account.address, 'pending')
                    base_fee = w3.eth.gas_price
                    gas_multiplier = 2 + attempt
                    
                    mint_tx = nft_contract.functions.mintNFT(
                        user_address,
                        tx_hash_bytes
                    ).build_transaction({
                        'from': admin_account.address,
                        'nonce': nonce,
                        'gas': 250000,  # Увеличили с 200k до 250k
                        'maxFeePerGas': base_fee * gas_multiplier,
                        'maxPriorityFeePerGas': w3.to_wei('0.01', 'gwei'),
                        'chainId': 8453
                    })
                    
                    signed = admin_account.sign_transaction(mint_tx)
                    mint_tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                    
                    log(f"⏳ Ждем подтверждения минта... TX: {mint_tx_hash.hex()}")
                    
                    receipt = w3.eth.wait_for_transaction_receipt(mint_tx_hash, timeout=90)
                    
                    if receipt.status == 1:
                        log(f"✅ NFT #{current_token_id + 1} заминчен для {user_address}!")
                        return jsonify({
                            "success": True,
                            "tx": mint_tx_hash.hex(),
                            "to": user_address,
                            "tokenId": current_token_id + 1,
                            "x402Version": 1
                        })
                    else:
                        if attempt < max_retries - 1:
                            log(f"❌ Минт провалился, повторяем...")
                            time.sleep(2)
                            continue
                        else:
                            log(f"❌ Минт провалился после {max_retries} попыток")
                            return jsonify({
                                "x402Version": 1,
                                "error": "Mint transaction failed"
                            }), 500
                
                except Exception as mint_error:
                    error_msg = str(mint_error)
                    log(f"⚠️ Попытка минта {attempt + 1} провалилась: {error_msg}")
                    
                    if 'nonce' in error_msg.lower() or 'replacement' in error_msg.lower():
                        if attempt < max_retries - 1:
                            log(f"⏳ Nonce conflict при минте, повторяем...")
                            time.sleep(3)
                            continue
                    
                    if attempt >= max_retries - 1:
                        raise mint_error
            
            return jsonify({
                "x402Version": 1,
                "error": "Mint failed after retries"
            }), 500
            
    except Exception as e:
        log(f"❌ Ошибка минта: {str(e)}")
        log(f"📜 Traceback:\n{traceback.format_exc()}")
        return jsonify({
            "x402Version": 1,
            "error": str(e)
        }), 500

@app.route('/api/info', methods=['GET'])
def info():
    """Информация о проекте (с кэшем)"""
    global info_cache
    
    # Проверяем кэш
    now = time.time()
    if info_cache["data"] and (now - info_cache["timestamp"]) < CACHE_TTL:
        return jsonify(info_cache["data"])
    
    # Обновляем кэш
    try:
        nft_contract = w3.eth.contract(
            address=Web3.to_checksum_address(NFT_CONTRACT), 
            abi=NFT_ABI
        )
        total_supply = nft_contract.functions.totalSupply().call()
        max_supply = nft_contract.functions.MAX_SUPPLY().call()
    except Exception as e:
        log(f"⚠️ Error reading contract: {e}")
        total_supply = "unknown"
        max_supply = 1000
    
    data = {
        "contract": NFT_CONTRACT,
        "price": MINT_PRICE,
        "price_usdc": MINT_PRICE / 1000000,
        "recipient": RECIPIENT_ADDRESS,
        "minted": total_supply,
        "maxSupply": max_supply
    }
    
    # Сохраняем в кэш
    info_cache["data"] = data
    info_cache["timestamp"] = now
    
    return jsonify(data)

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok"})

# CORS для всех endpoints
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,x-payment')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

if __name__ == '__main__':
    log("🚀 Запускаем NFT Mint API v2 (УЛУЧШЕННАЯ ВЕРСИЯ) на порту 5000...")
    log(f"📝 NFT Contract: {NFT_CONTRACT}")
    log(f"💰 Mint Price: {MINT_PRICE / 1000000} USDC")
    log(f"📬 Recipient: {RECIPIENT_ADDRESS}")
    log(f"🔒 Защита: Lock + Pending Nonce + Retry логика")
    
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

