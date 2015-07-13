#!/usr/bin/env python
import sys,os
import getopt,readline,atexit
import logging,traceback
import subprocess
from xml.dom import minidom
import datetime,dateutil.parser
import re,codecs

class ExitException(Exception):
    pass


class Colors:
    RED=91
    BLUE=94
    WHITE=97
    GREEN=92
    YELLOW=93
    def __init__(self,on):
        self.on=on
    def enable(self,on):
        self.on=on
    
    def color(self,val,text):
        if not self.on:
            return text
        return "\033["+str(val)+"m"+text+"\033[0m"

    def red(self,text):
        return self.color(self.RED,text)
    def blue(self,text):
        return self.color(self.BLUE,text)
    def white(self,text):
        return self.color(self.WHITE,text)
    def green(self,text):
        return self.color(self.GREEN,text)
    def yellow(self,text):
        return self.color(self.YELLOW,text)


class SvnMerge:
    def __init__(self):
        self.procs={}
        self.cmds={}
        self.clst=[]
        self.params={'source':'','revcount':'auto', 'showmerged':True, 'colors':True, 'verbose':False}
        self.col=Colors(self.params['colors'])
        self.canmerge=[]
        self.tomerge=[]
        self.merged=[]
        self.revs=[]
        self.revinfo={}
        self.test=False
        self.revsplit=re.compile(r"[\w\-:]+")
        self.branch=None

        self.log=logging.getLogger("merge")
        self.log.setLevel(logging.INFO)
        self.log.addHandler(logging.StreamHandler())

        self.addcmd("add|select|a|s|+",self.add,['add_revlist'])
        self.addcmd("remove|delete|unselect|r|d|-",self.remove,['del_revlist'])
        self.addcmd("merge",self.merge)
        self.addcmd("revert",self.revert)
        self.addcmd("commit",self.commit)
        self.addcmd("mergeinfo",self.mergeinfo)
        self.addcmd("update|up",self.update)
        self.addcmd("log|l",self.svnlog,["[number]"])
        self.addcmd("list",self.list,["[index]","[amount]"])
        self.addcmd("get",self.getparam,["[parameter]"])
        self.addcmd("set",self.setparam,["parameter","value"])
        self.addcmd("cd",self.cwd,["path"])
        self.addcmd("help|h",self.help,["[command|all]"])
        self.addcmd("quit|exit|q",self.quit)


    def addcmd(self,name,func,params=[]):
        self.procs[func]=[name,params]
        for x in name.split('|'):
            self.cmds[x]=func


    def quit(self,params):
        """ quit """
        if len(self.merged)>0:
            ans=raw_input("Uncommited merge found! Exit anyway? [y,N]")
            if not ans.lower() in ['y','yes']:
                return
        raise ExitException()

    def help(self,params):
        """ some help on commands """
        if len(params)==0:
            for x in self.procs:
                sys.stdout.write(self.procs[x][0]+"  ")
            sys.stdout.write("\n")
            return
        pr=params[0]
        if not pr in self.cmds and pr!="all":
            print "Unknown command",pr
            return
        for x in self.procs:
            if pr!="all" and self.cmds[pr]!=x:
                continue
            sys.stdout.write(self.procs[x][0])
            for p in self.procs[x][1]:
                sys.stdout.write(' '+p)
            sys.stdout.write(' - '+x.__doc__+"\n")


    def cwd(self,params):
        """ change working dorectory """
        os.chdir(params[0])
        print os.path.realpath(os.getcwd())

    def getparam(self,params):
        """ print options """
        for x in self.params:
            if len(params)==0 or x==params[0]:
                print x,"=",str(self.params[x])

    def setparam(self,params):
        """ set option """
        if not params[0] in self.params:
            raise Exception("Unknown option "+params[0])
        v=self.params[params[0]]
        if params[0]=='revcount':
            self.params[params[0]]='auto' if params[1]=='auto' else eval(params[1])
        else:
            if v.__class__.__name__=='bool':
                self.params[params[0]]=params[1] in ['1','true','on']
            elif v.__class__.__name__=='int':
                self.params[params[0]]=eval(params[1])
            else:
                self.params[params[0]]=params[1]
        print params[0],'=',str(self.params[params[0]])
        if params[0]=='verbose':
            self.log.setLevel(logging.DEBUG if self.params['verbose'] else logging.INFO)
        elif params[0]=="colors":
            self.col.enable(self.params['colors'])
        elif params[0] in ['showmerged','revcount']:
            self.list([])
        elif params[0]=="source":
            out=self.svn(["info",self.params['source']])
            url=""
            root=""
            for x in out.split("\n"):
                if x.startswith("URL: "):
                    url=x[5:]
                if x.startswith("Repository Root: "):
                    root=x[17:]
            self.branch=url.replace(root,"")[1:]
            print "Branch:",self.branch
            self.mergeinfo([])


    def update(self,params):
        """ update local branch """
        ret=os.system("svn update")
        if ret!=0:
            raise Exception("svn returns "+str(ret))

    def svnlog(self,params):
        """ log local revisions """
        ret=os.system("svn log -l "+("10" if len(params)<1 else params[0]))
        if ret!=0:
            raise Exception("svn returns "+str(ret))



    def updateLogs(self,frm,to):
        print "updating source logs..."
        cmd=["log","--xml"]
        if frm==0:
            cmd+=["-l",str(to)]
        else:
            cmd+=["-r",str(frm)+":"+str(to)]
        if self.test and frm==0 and os.path.exists("svnlog.log"):
            with open("svnlog.log","r") as f:
                out=f.read()
        else:
            out=self.svn(cmd+[self.params['source']])
            if self.test and frm==0:
                with open("svnlog.log","w") as f:
                    f.write(out)
        xml=minidom.parseString(out)
        for x in xml.documentElement.childNodes:
            if x.nodeName!="logentry":
                continue
            rs=x.getAttribute('revision')
            rev=int(rs)
            if not rev in self.revs:
                self.revs+=[rev]
            descr={'id':rev,'rev':rs,'author':'','date':'','msg':''}
            for n in x.childNodes:
                if not n.nodeName in descr:
                    continue
                val=n.childNodes[0].nodeValue
                descr[n.nodeName]=val
                if n.nodeName=='date':
                    descr['date']=dateutil.parser.parse(descr['date'])
            self.revinfo[rev]=descr
        self.revs.sort(reverse=True)


    def svn(self,params):
        self.log.debug("svn command %s",str(params))
        res=subprocess.check_output(["svn"]+params)
        self.log.debug("svn output:\n%s",str(res))
        return res


    def mergeinfo(self,params):
        """ update source revisions to merge """
        print "updating mergeinfo..."
        if (self.params['source']==''):
            raise Exception("Source not set")
        self.canmerge=[]
        self.revinfo={}
        self.revs=[]
        if self.test and os.path.exists("mergeinfo.log"):
            with open("mergeinfo.log","r") as f:
                out=f.read()
        else:
            out=self.svn(["mergeinfo","--show-revs","eligible",self.params['source']])
            if self.test:
                with open("mergeinfo.log","w") as f:
                    f.write(out)
        for x in out.split('\n'):
            x=x.strip()
            if x=='' or x[0]!='r':
                continue
            self.canmerge+=[int(x[1:])]
        self.canmerge.sort(reverse=True)
        if len(self.revinfo)==0:
            self.updateLogs(0,1000)
        self.list([])


    def list(self,params):
        """ show revision list """
        theight,twidth=os.popen('stty size', 'r').read().split()
        theight=int(theight)
        twidth=int(twidth)
        index=0 if len(params)<1 else int(params[0])
        count=self.params['revcount'] if len(params)<2 else int(params[1])
        if count=='auto':
            count=theight-5
        if count==0:
            count=10
        revs=self.revs if self.params['showmerged'] else self.canmerge
        if (index>=len(revs)):
            raise Exception("Wrong index "+str(index)+". Found "+str(len(revs))+" revisions.")
        if index+count>len(revs):
            count=len(revs)-index
        mn=revs[index+count-1]
        if not mn in self.revinfo:
            mx=min(self.revinfo) if len(self.revinfo)>0 else self.canmerge[0]
            self.updateLogs(mx,mn)
        self.printMerge(revs,index,count,twidth)


    def printMerge(self,revs,index,count,twidth):
        cols=[3,4,5,0]
        for x in range(count):
            rv=self.revinfo[revs[index+x]]
            if len(rv['rev'])>cols[0]:
                cols[0]=len(rv['rev'])
            if len(rv['author'])>cols[1]:
                cols[1]=len(rv['author'])
        print " REV "+' '*(cols[0]-3)+"USER "+' '*(cols[1]-4)+"DATE "+' '*(11-4)+"MSG"
        date=0
        dts=[""," Today:"," Yesterday:"," Last week:"," Older than one week:"]
        today=datetime.datetime.now().date()
        yesterday=today-datetime.timedelta(days=1)
        week=today-datetime.timedelta(days=7)
        cols[3]=twidth-(cols[0]+cols[1]+cols[2]+4)
        for x in range(count):
            rev=revs[index+x]
            rv=self.revinfo[rev]
            dt=rv['date'].date()
            if (dt==today):
                dt=1
            elif dt==yesterday:
                dt=2
            else:
                cols[2]=11
                cols[3]=twidth-(cols[0]+cols[1]+cols[2]+4)
                dt=3 if dt>=week else 4
            if dt!=date:
                date=dt
                print dts[date]
            self.printRevision(rv,rev,cols)


    def printRevision(self,rv,rev,cols):
        msg=rv['msg']
        dt=rv['date'].strftime("%H:%M" if cols[2]==5 else "%m-%d %H:%M")
        added=rev in self.tomerge
        merged=rev in self.merged
        cantmerge=rev not in self.canmerge
        st="+" if added else "*" if merged else "." if cantmerge else " "
        st+=rv['rev']+" "*(cols[0]-len(rv['rev'])+1)
        st+=rv['author']+" "*(cols[1]-len(rv['author'])+1)
        if (len(msg)>cols[3]):
            msg=msg[:cols[3]]
        msg=msg.replace("\n"," ").replace("\r","")
        st+=dt+" "+msg
        if merged:
            st=self.col.green(st)
        elif added:
            st=self.col.yellow(st)
        elif cantmerge:
            st=self.col.red(st)
        else:
            st=self.col.white(st)
        print st


    def makeRule(self,params):
        res=[]
        digs="0123456789"
        for x in ' '.join(params).split(','):
            x=x.strip()
            if x=='':
                continue
            if x[0] in digs or x[0]=='r' and x[1] in digs:
                if x.startswith('r'):
                    x=x[1:]
                if '-' in x or ':' in x:
                    p=x.split(':' if ':' in x else '-')
                    for i in range(2):
                        p[i]=p[i].strip()
                        if p[i].startswith('r'):
                            p[i]=p[i][1:]
                    x1=int(p[0])
                    x2=int(p[1])
                    x="id>="+str(min(x1,x2))+" and id<="+str(max(x1,x2))+""
                else:
                    x="id=="+x
            res+=['('+x+')']
        return ' and '.join(res)



    def add(self,params):
        """ add revisions to merge """
        rule=self.makeRule(params)
        try:
            for x in self.canmerge:
                if x in self.tomerge or x in self.merged or not x in self.revinfo:
                    continue
                if eval(rule,{},self.revinfo[x])==True:
                    self.tomerge+=[x]
        except Exception as e:
            print self.col.red("Error: "+str(e))
        self.tomerge.sort(reverse=True)
        self.list([])



    def remove(self,params):
        """ remove revisions from mergelist """
        rule=self.makeRule(params)
        try:
            for x in self.tomerge:
                if eval(rule,{},self.revinfo[x])==True:
                    self.tomerge.remove(x)
        except Exception as e:
            print self.col.red("Error: "+str(e))
        self.list([])


    def merge(self,params):
        """ merge selected revisions """
        if len(self.tomerge)==0:
            raise Exception("Nothing selected for merge")
        self.printMerge(self.tomerge,0,len(self.tomerge))
        ans=raw_input("Merge selected revisions? [y/N]:")
        if not ans.lower() in ['y','yes']:
            self.list([])
            return
        print "merging..."
        cmd="svn merge "
        for x in sorted(self.tomerge):
            cmd+="-c "+str(x)+" "
        cmd+=self.params['source']
        res=os.system(cmd)
        if res!=0:
            self.revert([])
            raise Exception("svn returns "+str(res))
        self.merged+=self.tomerge
        self.tomerge=[]
        self.list([])


    def revert(self,params):
        """ revert local branch """
        res=os.system("svn revert -R .")
        if res!=0:
            raise Exception("svn returns "+str(res))
        self.tomerge+=self.merged
        self.merged=[]
        self.list([])


    def commit(self,params):
        """ commit merge changes """
        if len(self.merged)==0:
            raise "Nothing to commit."
        revs=[]
        for x in sorted(self.merged):
            if len(revs)==0 or x!=revs[-1][1]+1:
                revs+=[[x,x]]
            else:
                revs[-1][1]=x
        msg="Merged revision(s) "
        for i,x in enumerate(revs):
            if i!=0:
                msg+=", "
            if x[0]==x[1]:
                msg+=str(x[0])
            else:
                msg+=str(x[0])+"-"+str(x[1])
        msg+=" from "+self.branch+":\n"
        for x in sorted(self.merged):
            msg+=self.revinfo[x]['msg']+"\n........\n"
        cfile="__commit_.log"
        with codecs.open(cfile,"w","utf-8") as f:
            f.write(msg)
        print msg
        ans=raw_input("Commit message in "+cfile+". Commit? [y/N]")
        if not ans in ['y','yes']:
            os.unlink(cfile)
            self.list([])
            return
        res=0
        if not self.test:
            res=os.system("svn commit -F "+cfile)
        os.unlink(cfile)
        if (res!=0):
            raise Exception("svn returns "+str(res))
        self.merged=[]
        self.mergeinfo([])




    def loop(self):
        cmd=raw_input(self.col.blue("merge> "))
        cmd=cmd.strip().lower()
        if cmd=='':
            return True
        lst=cmd.split()
        if not lst[0] in self.cmds:
            print self.col.red("Unknown command "+lst[0])
            return True
        try:
            proc=self.cmds[lst[0]]
            lst=lst[1:]
            prms=self.procs[proc][1]
            if len(lst)<len(prms):
                for x in range(len(lst),len(prms)):
                    if not prms[x].startswith('['):
                        raise Exception("Expected "+prms[x])
            proc(lst)
        except ExitException:
            return False
        except Exception as e:
            print self.col.red("Error: "+str(e))
            if self.params['verbose']:
                print traceback.format_exc()
        return True


    def cmdcompleter(self,text,state):
        def proc_rev(text):
            spl=[',',':','-']
            for x in spl:
                if x in text:
                    text=text.split(x)[-1]
            if text.startswith('r') or text.startswith('c'):
                text=text[1:]
            return text
        if state>0 and self.clst:
            return self.clst[state]
        txt=readline.get_line_buffer()
        while '  ' in txt:
            txt=txt.replace('  ',' ')
        words=txt.split(' ')
        if len(words)<2:
            what=['command']
        else:
            if not words[0] in self.cmds:
                return None
            proc=self.cmds[words[0]]
            prms=self.procs[proc][1]
            if len(prms)==1 and prms[0].endswith('revlist'):
                what=[prms[0]]
            else:
                words=words[1:]
                if len(prms)<len(words):
                    return None
                p=prms[len(words)-1]
                what=p.replace('[','').replace(']','').split('|')
        self.clst=[]
        for x in what:
            if x=='command':
                self.clst+=[c+" " for c in self.cmds if len(text)==0 or c.startswith(text)]
            elif x=='parameter':
                self.clst+=[c+" " for c in self.params if len(text)==0 or c.startswith(text)]
            elif x=='add_revlist':
                text=proc_rev(text)
                self.clst+=[str(c)+" " for c in self.canmerge if (len(text)==0 or str(c).startswith(text)) and not c in self.tomerge and not c in self.merged]
            elif x=='del_revlist':
                text=proc_rev(text)
                self.clst+=[str(c)+" " for c in self.tomerge if len(text)==0 or str(c).startswith(text)]
        return self.clst[0] if len(self.clst)>0 else None


    def usage(self):
        print """-=svn branch merge cui tool v0.2=-
usage: svnbmerge.py [options] [shell commands with parameters]
Options:
-h, --help      : Print usage
-v, --verbose   : Set verbose logging

Revision list legend:"""
        print self.col.white("  white     - merge candidate")
        print self.col.yellow("+ yellow    - selected for merge")
        print self.col.green("* green     - merged")
        print self.col.red(". red       - can't merge (already merged)")
        print """
Shell commands:"""
        self.help(["all"])


    def run(self):
        #proc args
        try:
            opts,args=getopt.getopt(sys.argv[1:],"hvt",["verbose","help"])
        except Exception as e:
            print str(e)
            return 1
        for o,a in opts:
            if o in ('-h','--help'):
                self.usage()
                return 1
            if o in ('-v','--verbose'):
                self.log.setLevel(logging.DEBUG)
                self.params['verbose']=True
            if o=="-t":
                self.test=True
        #run commands
        run=[]
        while len(args)>0:
            cmd=args[0]
            args=args[1:]
            if not cmd in self.cmds:
                print "Error: Unknown command",cmd
                return 1
            proc=self.cmds[cmd]
            params=[]
            for p in self.procs[proc][1]:
                if len(args)<1:
                    print "Error: Not enougth parameters for command",cmd
                    return 1           
                params+=[args[0]]
                args=args[1:] 
            run+=[(proc,params)]
        for x in run:
            x[0](x[1])
        #config readline
        histfile = os.path.join(os.path.expanduser("~/.svnbmerge"))
        try:
            readline.read_history_file(histfile)
        except IOError:
            pass
        atexit.register(readline.write_history_file,histfile)
        readline.parse_and_bind("tab: complete")
        readline.set_completer(self.cmdcompleter)
        #main loop
        while self.loop():
            pass
        return 0


if __name__=="__main__":
    sys.exit(SvnMerge().run())