import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import { useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';

export type SelectedPhoto = { uri: string; mimeType: 'image/jpeg' };
type SendMessage = (text: string, images?: SelectedPhoto[]) => Promise<'sent' | 'queued' | 'processing'>;
const maxPhotos = 8;

export function useCoachComposer(draft: string, consumeDraft: () => void, sendMessage: SendMessage) {
  const [text, setText] = useState('');
  const [images, setImages] = useState<SelectedPhoto[]>([]);
  const [sending, setSending] = useState(false);
  const [picking, setPicking] = useState(false);
  const sendingRef = useRef(false);
  const pickingRef = useRef(false);
  useEffect(() => { if (draft) { setText(draft); consumeDraft(); } }, [draft, consumeDraft]);

  async function chooseImage(source: 'camera' | 'library') {
    if (pickingRef.current || sendingRef.current) return;
    if (images.length >= maxPhotos) { Alert.alert('照片数量已达上限', '一条消息最多可以发送 8 张照片。'); return; }
    pickingRef.current = true; setPicking(true);
    try {
      if (source === 'camera') {
        const permission = await ImagePicker.requestCameraPermissionsAsync();
        if (!permission.granted) { Alert.alert('需要相机权限', '开启相机权限后，才能直接拍下饮食。'); return; }
      }
      const result = source === 'camera'
        ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.8 })
        : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.8, allowsMultipleSelection: true, selectionLimit: maxPhotos - images.length });
      if (result.canceled) return;
      const converted = await Promise.all(result.assets.slice(0, maxPhotos - images.length).map(async asset => {
        const context = ImageManipulator.manipulate(asset.uri);
        if (asset.width > 1600) context.resize({ width: 1600, height: null });
        const rendered = await context.renderAsync();
        const saved = await rendered.saveAsync({ compress: 0.72, format: SaveFormat.JPEG });
        return { uri: saved.uri, mimeType: 'image/jpeg' as const };
      }));
      setImages(current => [...current, ...converted].slice(0, maxPhotos));
    } catch { Alert.alert('暂时无法添加照片', '请重试，或换一张照片。'); }
    finally { pickingRef.current = false; setPicking(false); }
  }

  function addPhoto() {
    if (sendingRef.current || pickingRef.current) return;
    Alert.alert('添加饮食照片', '照片和文字会一起发送给教练。', [
      { text: '拍照', onPress: () => void chooseImage('camera') },
      { text: '从相册选择', onPress: () => void chooseImage('library') },
      { text: '取消', style: 'cancel' },
    ]);
  }

  async function send() {
    if ((!text.trim() && !images.length) || sendingRef.current || pickingRef.current) return;
    const messageText = text, messageImages = images;
    sendingRef.current = true; setSending(true); setText(''); setImages([]);
    try {
      const result = await sendMessage(messageText, messageImages);
      if (result === 'queued') Alert.alert('已放进待发送', '网络恢复后会自动发送，不需要再发一次。');
    } catch {
      // Do not overwrite a new draft composed while the previous request was in flight.
      setText(current => current ? `${messageText}\n${current}`.trim() : messageText);
      setImages(current => [...messageImages, ...current]);
    } finally { sendingRef.current = false; setSending(false); }
  }

  return { text, setText, images, sending, picking, send, addPhoto, removePhoto: (index: number) => setImages(current => current.filter((_, i) => i !== index)) };
}
