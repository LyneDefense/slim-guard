import AsyncStorage from '@react-native-async-storage/async-storage';
import { Directory, File, Paths } from 'expo-file-system';

import type { ChatPayload, PendingChat } from '../types';

const QUEUE_KEY = 'slimguard.pending-chat.v1';
const queueDirectory = new Directory(Paths.document, 'pending-chat');

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

export async function enqueueChat(
  payload: ChatPayload,
  imageUri?: string,
): Promise<PendingChat> {
  let savedImageUri: string | undefined;
  if (imageUri) {
    if (!queueDirectory.exists) queueDirectory.create({ intermediates: true, idempotent: true });
    const source = new File(imageUri);
    const extension = source.extension || '.jpg';
    const destination = new File(queueDirectory, `${payload.idempotency_key}${extension}`);
    await source.copy(destination);
    savedImageUri = destination.uri;
  }
  const { image_base64: _, ...storedPayload } = payload;
  const item: PendingChat = {
    payload: storedPayload,
    imageUri: savedImageUri,
    previewUri: savedImageUri,
    createdAt: new Date().toISOString(),
  };
  const queue = await readQueue();
  await writeQueue([...queue.filter((queued) => queued.payload.idempotency_key !== payload.idempotency_key), item]);
  return item;
}

export async function listPendingChats(): Promise<PendingChat[]> {
  return readQueue();
}

export async function hydratePendingChat(item: PendingChat): Promise<ChatPayload> {
  if (!item.imageUri) return item.payload;
  const file = new File(item.imageUri);
  if (!file.exists) throw new Error('离线照片已不可用，请重新选择');
  return { ...item.payload, image_base64: await file.base64() };
}

export async function removePendingChat(idempotencyKey: string): Promise<void> {
  const queue = await readQueue();
  const removed = queue.find((item) => item.payload.idempotency_key === idempotencyKey);
  await writeQueue(queue.filter((item) => item.payload.idempotency_key !== idempotencyKey));
  if (removed?.imageUri) {
    const file = new File(removed.imageUri);
    if (file.exists) file.delete();
  }
}
