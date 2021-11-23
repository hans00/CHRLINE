"""
Author: YinMo
Version: 1.0.0
Description: for pm.
"""
import hashlib
import json
import os
import base64
import axolotl_curve25519 as Curve25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

class E2EE():

    def getE2EELocalPublicKey(self, mid, keyId):
        fd = '.e2eePublicKeys'
        fn = f"key_id_{keyId}.json"
        key = self.getCacheData(fd, fn, False)
        if key is None:
            receiver_key_data = self.negotiateE2EEPublicKey(mid)
            if receiver_key_data[3] == -1:
                raise Exception(f'Not support E2EE on {mid}')
            receiverKeyId = receiver_key_data[2][2]
            receiverKeyData = receiver_key_data[2][4]
            if receiverKeyId == keyId:
                key = base64.b64encode(receiverKeyData)
                self.saveCacheData(fd, fn, key.decode(), False)
            else:
                raise Exception(f'E2EE key id-{keyId} not found on {mid}')
        return base64.b64decode(key)

    def generateSharedSecret(self, private_key, public_key):
        return Curve25519.calculateAgreement(bytes(private_key), bytes(public_key))
    
    def _xor(self, buf):
        buf_length = int(len(buf) / 2)
        buf2 = bytearray(buf_length)
        for i in range(buf_length):
            buf2[i] = buf[i] ^ buf[buf_length + i]
        return bytes(buf2)
    
    def getSHA256Sum(self, *args):
        instance = hashlib.sha256()
        for arg in args:
            if isinstance(arg, str):
                arg = arg.encode()
            instance.update(arg)
        return instance.digest()
    
    def _encryptAESECB(self, aes_key, plain_data):
        aes = AES.new(aes_key, AES.MODE_ECB)
        return aes.encrypt(plain_data)
    
    def decryptKeyChain(self, publicKey, privateKey, encryptedKeyChain):
        shared_secret = self.generateSharedSecret(privateKey, publicKey)
        aes_key = self.getSHA256Sum(shared_secret, 'Key')
        aes_iv = self._xor(self.getSHA256Sum(shared_secret, 'IV'))
        aes = AES.new(aes_key, AES.MODE_CBC, aes_iv)
        keychain_data = aes.decrypt(encryptedKeyChain)
        key = keychain_data.hex()
        key = bin2bytes(key)
        key = self.TCompactProtocol(key, passProtocol=True).res
        public_key = bytes(key[0][4])
        private_key = bytes(key[0][5])
        return [private_key, public_key]
    
    def encryptDeviceSecret (self, publicKey, privateKey, encryptedKeyChain):
        shared_secret = self.generateSharedSecret(privateKey, publicKey)
        aes_key = self.getSHA256Sum(shared_secret, 'Key')
        encryptedKeyChain = self._xor(self.getSHA256Sum(encryptedKeyChain))
        keychain_data = self._encryptAESECB(aes_key, encryptedKeyChain)
        return keychain_data
    
    def generateAAD(self, a, b, c, d, e=2, f=0):
        aad = b''
        aad += a.encode()
        aad += b.encode()
        aad += bytes(self.getIntBytes(c))
        aad += bytes(self.getIntBytes(d))
        aad += bytes(self.getIntBytes(e)) #e2ee version
        aad += bytes(self.getIntBytes(f)) # content type
        return aad
    
    def encryptE2EEMessage(self, to, text, specVersion=2):
        _from = self.mid
        selfKeyData = self.getE2EESelfKeyData(_from)
        if len(to) == 0 or self.getToType(to) != 0:
            raise Exception('Invalid mid')
        if selfKeyData is None:
            raise Exception('E2EE Key has not been saved, try register or use SQR Login')
        senderKeyId = selfKeyData['keyId']
        private_key = base64.b64decode(selfKeyData['privKey'])
        receiver_key_data = self.negotiateE2EEPublicKey(to)
        if receiver_key_data[3] == -1:
            raise Exception(f'Not support E2EE on {to}')
        receiverKeyId = receiver_key_data[2][2]
        keyData = self.generateSharedSecret(bytes(private_key), receiver_key_data[2][4])
        specVersion = receiver_key_data[3]
        chunks = self.encryptE2EETextMessage(senderKeyId, receiverKeyId, keyData, specVersion, text, to ,_from)
        return chunks
    
    def encryptE2EETextMessage(self, senderKeyId, receiverKeyId, keyData, specVersion, text, to ,_from):
        #selfKey = self.getE2EEKeys(self.mid)
        salt = os.urandom(16)
        gcmKey = self.getSHA256Sum(keyData, salt, b'Key')
        gcmIV = self.getSHA256Sum(keyData, salt, b'IV')
        aad = self.generateAAD(to, _from, senderKeyId, receiverKeyId, specVersion, 0)
        sign = os.urandom(16)
        data = json.dumps({
            'text': text
        }).encode()
        encData = self.encryptE2EEMessageV2(data, gcmKey, sign, aad)
        self.log(f'senderKeyId: {senderKeyId} ({self.getIntBytes(senderKeyId)})', True)
        self.log(f'receiverKeyId: {receiverKeyId} ({self.getIntBytes(receiverKeyId)})', True)
        return [salt, encData, sign, bytes(self.getIntBytes(senderKeyId)), bytes(self.getIntBytes(receiverKeyId))]
    
    def encryptE2EEMessageV2(self, data, gcmKey, nonce, aad):
        aesgcm = AESGCM(gcmKey)
        return aesgcm.encrypt(nonce, data, aad)
    
    def decryptE2EETextMessage(self, messageObj, isSelf=True):
        _from = messageObj[1]
        to = messageObj[2]
        toType = messageObj[3]
        metadata = messageObj[18]
        specVersion = metadata.get('e2eeVersion', '2')
        contentType = metadata.get('contentType', '0')
        chunks = messageObj[20]
        for i in range(len(chunks)):
            if isinstance(chunks[i], str):
                chunks[i] = chunks[i].encode()
        senderKeyId = byte2int(chunks[3])
        receiverKeyId = byte2int(chunks[4])
        self.log(f'senderKeyId: {senderKeyId}', True)
        self.log(f'receiverKeyId: {receiverKeyId}', True)
        
        selfKey = self.getE2EESelfKeyData(self.mid)
        targetKey = to
        targetKeyId = receiverKeyId
        if not isSelf:
            targetKey = _from
            targetKeyId = senderKeyId
        pubK = self.getE2EELocalPublicKey(to, targetKeyId)
        privK = base64.b64decode(selfKey['privKey'])
        
        if specVersion == '2':
            decrypted = self.decryptE2EEMessageV2(to , _from, chunks, privK, pubK, specVersion, contentType)
        else:
            decrypted = self.decryptE2EEMessageV1(chunks, privK, pubK)
        return decrypted.get('text', '')
    
    def decryptE2EEMessageV1(self, chunks, privK, pubK):
        salt = chunks[0]
        message = chunks[1]
        sign = chunks[2]
        aesKey = self.generateSharedSecret(privK, pubK)
        aes_key = self.getSHA256Sum(aesKey, salt, b'Key')
        aes_iv = fixedIV(self.getSHA256Sum(aesKey, salt, b'IV'))
        aes = AES.new(aes_key, AES.MODE_CBC, aes_iv)
        decrypted = aes.decrypt(message)
        self.log(f'decrypted: {decrypted}', True)
        decrypted = unpad(decrypted, 16)
        return json.loads(decrypted)
    
    def decryptE2EEMessageV2(self, to, _from, chunks, privK, pubK, specVersion=2, contentType=0):
        salt = chunks[0]
        message = chunks[1]
        sign = chunks[2]
        senderKeyId = byte2int(chunks[3])
        receiverKeyId = byte2int(chunks[4])
        
        aesKey = self.generateSharedSecret(privK, pubK)
        gcmKey = self.getSHA256Sum(aesKey, salt, b'Key')
        iv = self.getSHA256Sum(aesKey, salt, b'IV')
        aad = self.generateAAD(to, _from, senderKeyId, receiverKeyId, specVersion, contentType)
        
        aesgcm = AESGCM(gcmKey)
        decrypted = aesgcm.decrypt(sign, message, aad)
        self.log(f'decrypted: {decrypted}', True)
        return json.loads(decrypted)

def byte2int(t):
    e = 0
    i = 0
    s = len(t)
    for i in range(s):
        e = 256 * e + t[i]
    return e

def bin2bytes(k):
    e = []
    for i in range(int(len(k) / 2)):
        _i = int(k[i * 2:i * 2 + 2], 16)
        e.append(_i)
    return bytearray(e)

def fixedIV(k):
    e = []
    l = int(len(k) / 2)
    for i in range(l):
        _i = k[i] ^ k[l + i]
        e.append(_i)
    return bytearray(e)