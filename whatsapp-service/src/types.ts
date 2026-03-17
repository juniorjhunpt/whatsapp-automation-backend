export interface IncomingMessage {
  instanceId: string;
  from: string;
  fromName: string;
  message: string;
  messageId: string;
  messageType: string;
  timestamp: number;
  isGroup: boolean;
  groupId: string | null;
  imageBase64?: string;   // base64 da imagem (se houver)
  imageMime?: string;     // mime type da imagem
}

export interface OutgoingMessage {
  instanceId: string;
  to: string;
  message: string;
}

export interface InstanceStatus {
  id: string;
  status: 'connecting' | 'qr' | 'connected' | 'disconnected';
  phone?: string;
}

export interface QREvent {
  instanceId: string;
  qr: string; // base64 png
}

export interface StatusEvent {
  instanceId: string;
  status: string;
  phone?: string;
}
