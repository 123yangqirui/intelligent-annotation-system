const {contextBridge,ipcRenderer}=require('electron')
contextBridge.exposeInMainWorld('collector',{call:(action,payload)=>ipcRenderer.invoke('collector',action,payload)})
