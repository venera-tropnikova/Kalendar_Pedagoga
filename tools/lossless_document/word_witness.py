"""Read-only independent Word importer witness for legacy DOC conversion QA."""
from pathlib import Path
import json,sys,re
import pythoncom
import win32com.client

output=Path(sys.argv[1]); sources=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
pythoncom.CoInitialize()
app=None
snapshots=[]
try:
 app=win32com.client.DispatchEx('Word.Application')
 app.Visible=False
 app.DisplayAlerts=0
 app.AutomationSecurity=3
 old_links=app.Options.UpdateLinksAtOpen
 app.Options.UpdateLinksAtOpen=False
 print('WORD_VERSION',str(app.Version),flush=True)
 for item in sources:
  doc=None
  try:
   doc=app.Documents.Open(FileName=str(Path(item['path']).resolve()),ConfirmConversions=False,ReadOnly=True,AddToRecentFiles=False,Visible=False,OpenAndRepair=False,NoEncodingDialog=True)
   value={'key':item['key'],'path':item['path'],'format':item['format'],'word_version':str(app.Version),'main_paragraph_count':doc.Paragraphs.Count,'main_table_count':doc.Tables.Count,'stories':[]}
   for story in doc.StoryRanges:
    current=story
    count=0
    while current is not None:
     count+=1
     if count>1000:raise RuntimeError('Story cycle')
     value['stories'].append({'type':int(current.StoryType),'text':str(current.Text),'paragraph_count':current.Paragraphs.Count,'table_count':current.Tables.Count,'fields':[{'code':str(f.Code.Text),'result':str(f.Result.Text)} for f in current.Fields]})
     current=current.NextStoryRange
   snapshots.append(value)
   output.write_text(json.dumps(snapshots,ensure_ascii=False,indent=2),encoding='utf-8')
   print(item['key'],item['format'],value['main_paragraph_count'],value['main_table_count'],len(value['stories']),flush=True)
  finally:
   if doc is not None:doc.Close(SaveChanges=0)
finally:
 if app is not None:
  try:app.Options.UpdateLinksAtOpen=old_links
  finally:app.Quit(SaveChanges=0)
 pythoncom.CoUninitialize()
