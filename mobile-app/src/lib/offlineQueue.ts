import AsyncStorage from '@react-native-async-storage/async-storage';
import { Directory, File, Paths } from 'expo-file-system';
import type { ChatPayload, PendingChat } from '../types';
const QUEUE_KEY = 'slimguard.pending-chat.v1';
const queueDirectory = new Directory(Paths.document, 'pending-chat');
let pendingWrite: Promise<unknown> = Promise.resolve();
function mutate<T>(action: () => Promise<T>): Promise<T> {
  const result = pendingWrite.then(action, action);
  pendingWrite = result.catch(() => {});
  return result;
}
async function readQueue(): Promise<PendingChat[]> {
  const raw = await AsyncStorage.getItem(QUEUE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as PendingChat[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
async function writeQueue(queue: PendingChat[]): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
}
export async function enqueueChat(payload: ChatPayload, imageUris: string[] = [], ownerId?: string, imageMimeTypes?: PendingChat['imageMimeTypes']): Promise<PendingChat> {
  return mutate(async () => {
    const savedImageUris: string[] = [];
    if (imageUris.length > 0) {
      if (!queueDirectory.exists) queueDirectory.create({
        intermediates: true,
        idempotent: true
      });
      for (const [index, imageUri] of imageUris.entries()) {
        const source = new File(imageUri);
        const extension = source.extension || '.jpg';
        const destination = new File(queueDirectory, `${payload.idempotency_key}-${index}${extension}`);
        await source.copy(destination);
        savedImageUris.push(destination.uri);
      }
    }
    const {
      image_base64: _legacyImage,
      images: _images,
      ...storedPayload
    } = payload;
    const item: PendingChat = {
      payload: storedPayload,
      imageUris: savedImageUris,
      imageMimeTypes,
      previewUri: savedImageUris[0],
      createdAt: new Date().toISOString(),
      ownerId,
      deliveryState: 'queued'
    };
    const queue = await readQueue();
    await writeQueue([...queue.filter(queued => queued.payload.idempotency_key !== payload.idempotency_key), item]);
    return item;
  });
}
export async function listPendingChats(): Promise<PendingChat[]> {
  await pendingWrite;
  return readQueue();
}
export async function markPendingChat(idempotencyKey: string, deliveryState: 'queued' | 'processing'): Promise<void> {
  return mutate(async () => {
    const queue = await readQueue();
    await writeQueue(queue.map(item => item.payload.idempotency_key === idempotencyKey ? {
      ...item,
      deliveryState
    } : item));
  });
}
export async function hydratePendingChat(item: PendingChat): Promise<ChatPayload> {
  const imageUris = item.imageUris?.length ? item.imageUris : item.imageUri ? [item.imageUri] : [];
  if (!imageUris.length) return item.payload;
  const images = [];
  for (const [index, uri] of imageUris.entries()) {
    const file = new File(uri);
    if (!file.exists) throw new Error('离线照片已不可用，请重新选择');
    const mime = item.imageMimeTypes?.[index] || (file.extension === '.png' ? 'image/png' : file.extension === '.webp' ? 'image/webp' : 'image/jpeg');
    images.push({
      image_base64: await file.base64(),
      mime_type: mime
    });
  }
  if (item.imageUri && !item.imageUris?.length) {
    return {
      ...item.payload,
      image_base64: images[0].image_base64,
      image_mime_type: 'image/jpeg'
    };
  }
  return {
    ...item.payload,
    images
  };
}
export async function removePendingChat(idempotencyKey: string): Promise<void> {
  return mutate(async () => {
    const queue = await readQueue();
    const removed = queue.find(item => item.payload.idempotency_key === idempotencyKey);
    await writeQueue(queue.filter(item => item.payload.idempotency_key !== idempotencyKey));
    const imageUris = removed?.imageUris?.length ? removed.imageUris : removed?.imageUri ? [removed.imageUri] : [];
    for (const uri of imageUris) {
      const file = new File(uri);
      if (file.exists) file.delete();
    }
  });
}
