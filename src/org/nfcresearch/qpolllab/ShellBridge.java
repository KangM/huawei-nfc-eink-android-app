package org.nfcresearch.qpolllab;
import android.os.*;
import android.content.pm.ApplicationInfo;
import java.io.*;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/** Shizuku service with fixed operations and bounded Binder messages. */
public final class ShellBridge extends Binder {
 private final int appUid; private final String apk;
 private final Object lock=new Object();private final StringBuilder logs=new StringBuilder();
 private volatile boolean active,cancelled;private volatile File stop;
 public ShellBridge()throws Exception {
  if(android.os.Process.myUid()!=0&&android.os.Process.myUid()!=2000)throw new SecurityException("Shell/root required");
  Object pm=Class.forName("android.app.ActivityThread").getMethod("getPackageManager").invoke(null);
  ApplicationInfo info=(ApplicationInfo)Class.forName("android.content.pm.IPackageManager").getMethod("getApplicationInfo",String.class,long.class,int.class).invoke(pm,"org.nfcresearch.qpolllab",0L,0);
  if(info==null)throw new IllegalStateException("App missing");appUid=info.uid;apk=info.sourceDir;
 }
 private void log(String line){synchronized(lock){logs.append(line).append('\n');if(logs.length()>50000)logs.delete(0,logs.indexOf("\n",logs.length()-40000)+1);}}
 private void cancel(){cancelled=true;File file=stop;if(file!=null)try{file.createNewFile();}catch(Exception ignored){}}
 @Override protected boolean onTransact(int code,Parcel data,Parcel reply,int flags)throws RemoteException {
  if(code==16777115){cancel();new Thread(()->{long deadline=SystemClock.elapsedRealtime()+330000;while(active&&SystemClock.elapsedRealtime()<deadline)SystemClock.sleep(100);System.exit(0);},"nfc-destroy").start();return true;}
  if(code<1||code>3)return super.onTransact(code,data,reply,flags);
  if(Binder.getCallingUid()!=appUid)throw new SecurityException("Wrong caller");data.enforceInterface("org.nfcresearch.qpolllab.NfcSession");
  if(code==1){String mode=data.readString();int seconds=data.readInt();String json=data.readString();byte[] image=data.createByteArray();IBinder owner=data.readStrongBinder();
   if(mode==null||!mode.matches("inspect|standard|qtag-sweep|qtag-hold|qtag|identity|refresh")||seconds<1||seconds>60||json==null||json.length()>8192||image==null||image.length>105984||owner==null)throw new IllegalArgumentException("Invalid NFC session");
   synchronized(lock){if(active)throw new IllegalStateException("另一会话尚未结束");active=true;cancelled=false;logs.setLength(0);}
   IBinder.DeathRecipient death=this::cancel;try{owner.linkToDeath(death,0);}catch(RemoteException e){active=false;throw e;}
   new Thread(()->{try{run(mode,seconds,json,image);}catch(Exception e){log("QP|ERROR|"+e);}finally{owner.unlinkToDeath(death,0);synchronized(lock){logs.append("QP|BRIDGE_END|后端会话结束\n");active=false;}}},"nfc-session").start();reply.writeNoException();return true;
  }
  if(code==2){synchronized(lock){reply.writeNoException();reply.writeString(logs.toString());logs.setLength(0);reply.writeInt(active?1:0);}return true;}
  cancel();reply.writeNoException();return true;
 }
 private void run(String mode,int seconds,String json,byte[] bytes)throws Exception {
  File dir=new File("/data/local/tmp/qpoll-bridge-"+android.os.Process.myPid()+"-"+System.nanoTime());if(!dir.mkdir())throw new IOException("Cannot create session directory");
  if(new ProcessBuilder("chmod","700",dir.toString()).start().waitFor()!=0)throw new IOException("Cannot protect session directory");
  File mark=new File(dir,"stop"),job=new File(dir,"job.json"),image=new File(dir,"image.bin");stop=mark;
  java.lang.Process child=null;
  try{
   if(cancelled)mark.createNewFile();JSONObject settings=new JSONObject(json);if(bytes.length>0)settings.put("imageFile",image.toString());
   try(FileOutputStream out=new FileOutputStream(image)){out.write(bytes);}try(FileOutputStream out=new FileOutputStream(job)){out.write(settings.toString().getBytes("UTF-8"));}
   java.util.ArrayList<String> command=new java.util.ArrayList<>();java.util.Collections.addAll(command,"/system/bin/app_process","/system/bin","org.nfcresearch.qpolllab.QPollWorker",mode,""+seconds,mark.toString(),new File(dir,"session.lock").toString());if(mode.equals("identity")||mode.equals("refresh"))command.add(job.toString());
   ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true);builder.environment().put("CLASSPATH",apk);child=builder.start();final java.lang.Process running=child;
   Thread deadline=new Thread(()->{try{if(!running.waitFor(320,TimeUnit.SECONDS)){mark.createNewFile();if(!running.waitFor(10,TimeUnit.SECONDS))running.destroy();}}catch(Exception ignored){}});deadline.setDaemon(true);deadline.start();
   try(BufferedReader in=new BufferedReader(new InputStreamReader(child.getInputStream(),"UTF-8"))){String line;while((line=in.readLine())!=null)log(line);}
   int exit=child.waitFor();if(exit!=0)log("QP|PROCESS_EXIT|"+exit);
  }finally{mark.createNewFile();if(child!=null&&!child.waitFor(10,TimeUnit.SECONDS))child.destroy();stop=null;job.delete();image.delete();mark.delete();new File(dir,"session.lock").delete();dir.delete();}
 }
}
