// Node 下载器在部分网络中会长时间等待；使用 curl，仍保留官方包内 SHA-256 校验。
const {downloadArtifact}=require('@electron/get')
const {extract}=require('@electron-internal/extract-zip')
const {spawn,spawnSync}=require('node:child_process')
const fs=require('node:fs/promises')
const path=require('node:path')
async function main(){
  const root=path.dirname(require.resolve('electron/package.json'))
  const version=require('electron/package.json').version
  const zip=await downloadArtifact({version,artifactName:'electron',platform:process.platform,arch:process.arch,checksums:require('electron/checksums.json'),downloader:{download:(url,target)=>new Promise((resolve,reject)=>{
    const hasAria=spawnSync('aria2c',['--version'],{stdio:'ignore'}).status===0
    const command=hasAria?'aria2c':'curl'
    const args=hasAria?['--max-connection-per-server=16','--split=16','--min-split-size=1M','--file-allocation=none','--allow-overwrite=true','--auto-file-renaming=false','--summary-interval=30','--console-log-level=warn','--download-result=hide','--timeout=30','--max-tries=3','--dir',path.dirname(target),'--out',path.basename(target),url]:['--fail','--location','--retry','2','--max-time','180','--output',target,url]
    const child=spawn(command,args,{stdio:'inherit'})
    child.on('error',reject);child.on('close',code=>code===0?resolve():reject(new Error('Electron 下载失败：'+code)))
  })}})
  await extract(zip,{dir:path.join(root,'dist')})
  const executable=process.platform==='darwin'?'Electron.app/Contents/MacOS/Electron':process.platform==='win32'?'electron.exe':'electron'
  await fs.writeFile(path.join(root,'path.txt'),executable)
  console.log('Electron 运行时已安装并通过包内校验。')
}
main().catch(error=>{console.error(error.message);process.exit(1)})
