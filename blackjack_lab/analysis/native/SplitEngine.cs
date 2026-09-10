// Exact shared-shoe solver. Managed acceleration; no sampling or probability truncation.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Web.Script.Serialization;

struct Key : IEquatable<Key> {
    public ulong C; public int State;
    public Key(ulong c,int state) { C=c; State=state; }
    public bool Equals(Key other) { return C==other.C && State==other.State; }
    public override bool Equals(object other) { return other is Key && Equals((Key)other); }
    public override int GetHashCode() { unchecked {
        ulong x=C ^ ((ulong)(uint)State*0x9e3779b97f4a7c15UL);
        x^=x>>33; x*=0xff51afd7ed558ccdUL; x^=x>>33; x*=0xc4ceb9fe1a85ec53UL; x^=x>>33;
        return (int)x;
    }}
}

struct D7 {
    public double B,S17,S18,S19,S20,S21,Bust;
    public void Add(D7 d,double p) { B+=d.B*p; S17+=d.S17*p; S18+=d.S18*p; S19+=d.S19*p; S20+=d.S20*p; S21+=d.S21*p; Bust+=d.Bust*p; }
    public void End(int score,double p) { if(score>21) Bust+=p; else switch(score) {case 17:S17+=p;break;case 18:S18+=p;break;case 19:S19+=p;break;case 20:S20+=p;break;case 21:S21+=p;break;} }
    public double Get(int i) { switch(i) {case 0:return B;case 1:return S17;case 2:return S18;case 3:return S19;case 4:return S20;case 5:return S21;default:return Bust;} }
    public double Value(int score) { if(score>21)return -1; double value=Bust-B; for(int i=1;i<6;i++)value+=(score>i+16?1:score<i+16?-1:0)*Get(i);return value; }
}

// A replacement cache only re-evaluates collisions; it never omits a branch.
class DealerMemo {
    const int Bits=22, Mask=(1<<Bits)-1;
    readonly ulong[] keys=new ulong[1<<Bits];
    readonly byte[] states=new byte[1<<Bits];
    readonly D7[] values=new D7[1<<Bits];
    public int Count;
    int Slot(Key key) {return key.GetHashCode()&Mask;}
    public bool TryGetValue(Key key,out D7 value) {int i=Slot(key);if(keys[i]==key.C && states[i]==key.State+1){value=values[i];return true;}value=new D7();return false;}
    public D7 this[Key key] {set{int i=Slot(key);if(states[i]==0)Count++;keys[i]=key.C;states[i]=(byte)(key.State+1);values[i]=value;}}
}

struct D9 {
    public double A,B,C,D,E,F,G,H,I;
    public double Get(int i) {switch(i){case 0:return A;case 1:return B;case 2:return C;case 3:return D;case 4:return E;case 5:return F;case 6:return G;case 7:return H;default:return I;}}
    public void Put(int i,double p) {switch(i){case 0:A+=p;break;case 1:B+=p;break;case 2:C+=p;break;case 3:D+=p;break;case 4:E+=p;break;case 5:F+=p;break;case 6:G+=p;break;case 7:H+=p;break;case 8:I+=p;break;}}
    public void Add(D9 d,double p) {A+=d.A*p; B+=d.B*p; C+=d.C*p; D+=d.D*p; E+=d.E*p; F+=d.F*p; G+=d.G*p; H+=d.H*p; I+=d.I*p;}
    public double EV() {return -2*A-B-D+F+H+2*I;}
}

class SplitEngine {
    const double Epsilon=1e-12;
    readonly int up,excluded; bool aces; readonly double budget;
    readonly Stopwatch watch=Stopwatch.StartNew();
    long nodes;
    int h2; bool a2,force2;
    readonly DealerMemo dealerCache=new DealerMemo();
    readonly Dictionary<ulong,D7> endCache=new Dictionary<ulong,D7>();
    readonly Dictionary<Key,double> secondCache=new Dictionary<Key,double>();
    readonly Dictionary<Key,double> firstCache=new Dictionary<Key,double>();
    readonly Dictionary<Key,D9> secondDistCache=new Dictionary<Key,D9>();
    readonly Dictionary<Key,D9> firstDistCache=new Dictionary<Key,D9>();
    public SplitEngine(int dealerUp,bool peek,bool splitAces,double seconds) {up=dealerUp;excluded=peek&&up==1?9:peek&&up==10?0:-1;aces=splitAces;budget=seconds;}
    void Check() {nodes++;if((nodes&1023)==1 && watch.Elapsed.TotalSeconds>=budget)throw new InvalidOperationException("TIMEOUT");}
    static int Score(int h,bool a) {return a&&h<=11?h+10:h;}
    static int Shift(int i) {return i*6;}
    static int Count(ulong c,int i) {return (int)((c>>Shift(i))&(i==9?255UL:63UL));}
    static ulong Remove(ulong c,int i) {return c-(1UL<<Shift(i));}
    static int Size(ulong c) {return (int)((c&63UL)+((c>>6)&63UL)+((c>>12)&63UL)+((c>>18)&63UL)+((c>>24)&63UL)+((c>>30)&63UL)+((c>>36)&63UL)+((c>>42)&63UL)+((c>>48)&63UL)+(c>>54));}
    static bool SafeLow(int size,int h,bool a) {return size>=64 && h<=11 && Score(h,a)<17;}
    int Mass(ulong c,int size) {return size-(excluded>=0?Count(c,excluded):0);}
    double Draw(ulong c,int i,int size,int mass) {return (double)Count(c,i)*(mass-(i!=excluded?1:0))/mass/(size-1);}
    D7 HardTail(ulong c,int h,int size) {
        if(size==0)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        D7 d=new D7();int low=17-h;
        d.S17=(double)Count(c,low-1)/size;d.S18=(double)Count(c,low)/size;
        d.S19=(double)Count(c,low+1)/size;d.S20=(double)Count(c,low+2)/size;d.S21=(double)Count(c,low+3)/size;
        int continuing=0;for(int v=1;v<low;v++){int n=Count(c,v-1);continuing+=n;if(n>0)d.Add(HardTail(Remove(c,v-1),h+v,size-1),(double)n/size);}
        int terminal=0;for(int v=low;v<=low+4;v++)terminal+=Count(c,v-1);
        d.Bust+=(double)(size-continuing-terminal)/size;return d;
    }
    D7 DealerRun(ulong c,int h,bool a) {
        Check();if(h>=14)return HardTail(c,h,Size(c));a=a&&h<=11;Key key=new Key(c,h*2+(a?1:0));D7 found;
        if(dealerCache.TryGetValue(key,out found))return found;
        int size=Size(c);if(size==0)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        D7 result=new D7();
        for(int i=0;i<10;i++){int n=Count(c,i);if(n==0)continue;double p=(double)n/size;int nh=h+i+1;bool na=a||i==0;int score=Score(nh,na);
            if(score>=17)result.End(score,p);else result.Add(DealerRun(Remove(c,i),nh,na),p);}
        dealerCache[key]=result;return result;
    }
    D7 Dealer(ulong c) {
        Check();D7 found;if(endCache.TryGetValue(c,out found))return found;
        int size=Size(c),mass=Mass(c,size);if(mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");D7 result=new D7();
        for(int i=0;i<10;i++){int n=Count(c,i);if(n==0||i==excluded)continue;double p=(double)n/mass;
            if((up==1&&i==9)||(up==10&&i==0)){result.B+=p;continue;}
            int h=up+i+1;bool a=up==1||i==0;int score=Score(h,a);
            if(score>=17)result.End(score,p);else result.Add(DealerRun(Remove(c,i),h,a),p);}
        endCache[c]=result;return result;
    }
    double Stand(ulong c,int score) {return score>21?-1:Dealer(c).Value(score);}
    double Second(ulong c,int h,bool a,bool force) {
        Check();if(h>21)return -1;a=a&&h<=11;Key key=new Key(c,h*4+(a?2:0)+(force?1:0));double value;if(secondCache.TryGetValue(key,out value))return value;
        int size=Size(c),mass=Mass(c,size),score=Score(h,a);
        bool safe=!aces&&SafeLow(size,h,a);
        if(!force&&!safe){value=Stand(c,score);if(aces||score>=21||size<2){secondCache[key]=value;return value;}
            double bust=0;for(int i=0;i<10;i++)if(Score(h+i+1,a||i==0)>21)bust+=Draw(c,i,size,mass);
            if(1-2*bust<=value+Epsilon){secondCache[key]=value;return value;}}
        else value=0;
        if(size<2||mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        double hitting=0;for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)hitting+=p*Second(Remove(c,i),h+i+1,a||i==0,false);}
        if(force||safe||hitting>value+Epsilon)value=hitting;secondCache[key]=value;return value;
    }
    double FirstStand(ulong c,int h,bool a) {return Stand(c,Score(h,a))+Second(c,h2,a2,force2);}
    double First(ulong c,int h,bool a,bool force) {
        Check();a=a&&h<=11;Key key=new Key(c,h*4+(a?2:0)+(force?1:0));double value;if(firstCache.TryGetValue(key,out value))return value;
        int size=Size(c),mass=Mass(c,size),score=Score(h,a);
        bool safe=!aces&&SafeLow(size,h,a);
        if(!force&&!safe){value=FirstStand(c,h,a);if(aces||score>=21||size<2){firstCache[key]=value;return value;}
            double bust=0;for(int i=0;i<10;i++)if(Score(h+i+1,a||i==0)>21)bust+=Draw(c,i,size,mass);
            if(2-2*bust<=value+Epsilon){firstCache[key]=value;return value;}}
        else value=0;
        if(size<2||mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        double hitting=0;for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)hitting+=p*First(Remove(c,i),h+i+1,a||i==0,false);}
        if(force||safe||hitting>value+Epsilon)value=hitting;firstCache[key]=value;return value;
    }
    D9 Terminal(ulong c,int s1,int s2) {
        Check();D9 result=new D9();if(s1>21&&s2>21){result.A=1;return result;}D7 dealer=Dealer(c);
        for(int i=0;i<7;i++){double p=dealer.Get(i);if(p==0)continue;int a=s1>21||i==0?-1:i==6||s1>i+16?1:s1==i+16?0:-1;
            int b=s2>21||i==0?-1:i==6||s2>i+16?1:s2==i+16?0:-1;result.Put((a+1)*3+b+1,p);}return result;
    }
    D9 SecondDist(ulong c,int s1,int h,bool a,bool force) {
        Check();a=a&&h<=11;Key key=new Key(c,(h*4+(a?2:0)+(force?1:0))*32+s1);D9 result;
        if(secondDistCache.TryGetValue(key,out result))return result;
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        bool hit=force||(!aces&&SafeLow(size,h,a))||(!aces&&score<21&&size>=2&&Second(c,h,a,false)>Stand(c,score)+Epsilon);
        if(!hit)result=Terminal(c,s1,Math.Min(score,22));else{
            if(size<2||mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");result=new D9();
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)result.Add(SecondDist(Remove(c,i),s1,h+i+1,a||i==0,false),p);}}
        secondDistCache[key]=result;return result;
    }
    D9 FirstDist(ulong c,int h,bool a,bool force) {
        Check();a=a&&h<=11;Key key=new Key(c,h*4+(a?2:0)+(force?1:0));D9 result;if(firstDistCache.TryGetValue(key,out result))return result;
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        bool hit=force||(!aces&&SafeLow(size,h,a))||(!aces&&score<21&&size>=2&&First(c,h,a,false)>FirstStand(c,h,a)+Epsilon);
        if(!hit)result=SecondDist(c,Math.Min(score,22),h2,a2,force2);else{
            if(size<2||mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");result=new D9();
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)result.Add(FirstDist(Remove(c,i),h+i+1,a||i==0,false),p);}}
        firstDistCache[key]=result;return result;
    }
    static int[] Ints(object value) {object[] src=(object[])value;int[] result=new int[src.Length];for(int i=0;i<src.Length;i++)result[i]=Convert.ToInt32(src[i]);return result;}
    object Result(D9 d) {
        var net=new Dictionary<string,double>();for(int v=-2;v<=2;v++)net[v.ToString()]=0;
        var joint=new Dictionary<string,double>();double one=0,two=0;
        for(int i=0;i<9;i++){int a=i/3-1,b=i%3-1;double p=d.Get(i);net[(a+b).ToString()]+=p;joint[a+","+b]=p;one+=a*p;two+=b*p;}
        return new Dictionary<string,object>{{"ev",d.EV()},{"net_distribution",net},{"joint_distribution",joint},{"hand_evs",new double[]{one,two}}};
    }
    object SingleResult(D9 d,int scale) {
        // The first coordinate here is a fixed -1 sentinel. Only the second
        // coordinate is the real unsplit hand; no extra physical card is drawn.
        return new Dictionary<string,object>{{"ev",scale*(d.C-d.A)},
            {"net_distribution",new Dictionary<string,double>{{(-scale).ToString(),d.A},{"0",d.B},{scale.ToString(),d.C}}}};
    }
    Dictionary<string,object> Single(ulong c,int[] player,object[] requested,Dictionary<string,object> actions) {
        bool savedAces=aces;aces=false;
        int h=0;bool a=false;foreach(int v in player){h+=v;a|=v==1;}
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        if(size<2||mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        D7 dealer=Dealer(c);double bust=0;
        var draw=new Dictionary<string,double>();string[] labels={"A","2","3","4","5","6","7","8","9","T"};
        for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);draw[labels[i]]=p;if(Score(h+i+1,a||i==0)>21)bust+=p;}
        var dealerValues=new Dictionary<string,double>();string[] endings={"blackjack","17","18","19","20","21","bust"};
        for(int i=0;i<7;i++)dealerValues[endings[i]]=dealer.Get(i);
        foreach(object requestedAction in requested){string action=(string)requestedAction;
            if(action=="stand"){
                if(player.Length==2&&a&&score==21)actions[action]=new Dictionary<string,object>{{"ev",1.5*(1-dealer.B)},
                    {"net_distribution",new Dictionary<string,double>{{"0",dealer.B},{"1.5",1-dealer.B}}}};
                else actions[action]=SingleResult(Terminal(c,22,score),1);
            } else if(action=="surrender")actions[action]=new Dictionary<string,object>{{"ev",-.5},
                {"net_distribution",new Dictionary<string,double>{{"-0.5",1}}}};
            else if(action=="hit"&&score<21)actions[action]=SingleResult(SecondDist(c,22,h,a,true),1);
            else if(action=="double"&&score<21&&player.Length==2){D9 d=new D9();
                for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)d.Add(Terminal(Remove(c,i),22,Math.Min(Score(h+i+1,a||i==0),22)),p);}
                actions[action]=SingleResult(d,2);
            }
        }
        aces=savedAces;
        if(aces){secondCache.Clear();secondDistCache.Clear();}
        return new Dictionary<string,object>{{"next_draw",draw},{"hit_bust",bust},{"dealer_distribution",dealerValues}};
    }
    object Solve(Dictionary<string,object> input) {
        int[] counts=Ints(input["counts"]);if(counts.Length!=10)throw new ArgumentException("COUNT_LENGTH");ulong c=0;
        for(int i=0;i<10;i++){if(counts[i]<0||counts[i]>(i==9?128:32))throw new ArgumentException("COUNT_RANGE");c|=(ulong)counts[i]<<Shift(i);}
        object[] hs=(object[])input["hands"];int[] one=Ints(hs[0]),two=Ints(hs[1]);int h1=0;bool a1=false;h2=0;a2=false;
        foreach(int v in one){h1+=v;a1|=v==1;}foreach(int v in two){h2+=v;a2|=v==1;}force2=two.Length==1||(Convert.ToInt32(input["active"])==1&&input.ContainsKey("force_active")&&(bool)input["force_active"]);
        int active=Convert.ToInt32(input["active"]);var actions=new Dictionary<string,object>();
        Dictionary<string,object> probabilities=null;
        if(input.ContainsKey("single_player")){
            object[] requested=(object[])input["single_actions"];
            probabilities=Single(c,Ints(input["single_player"]),requested,actions);
            if(Array.IndexOf(requested,"split")>=0)actions["split"]=Result(FirstDist(c,h1,a1,true));
        }
        else if(active==2)actions["complete"]=Result(Terminal(c,Math.Min(Score(h1,a1),22),Math.Min(Score(h2,a2),22)));
        else if(active==0){if(one.Length==1||(input.ContainsKey("force_active")&&(bool)input["force_active"]))actions["deal"]=Result(FirstDist(c,h1,a1,true));else{
            actions["stand"]=Result(SecondDist(c,Math.Min(Score(h1,a1),22),h2,a2,force2));
            if(!aces&&Score(h1,a1)<21){D9 d=new D9();int size=Size(c),mass=Mass(c,size);for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)d.Add(FirstDist(Remove(c,i),h1+i+1,a1||i==0,false),p);}actions["hit"]=Result(d);}}}
        else {int s1=Math.Min(Score(h1,a1),22);if(force2)actions["deal"]=Result(SecondDist(c,s1,h2,a2,true));else{
            actions["stand"]=Result(Terminal(c,s1,Math.Min(Score(h2,a2),22)));
            if(!aces&&Score(h2,a2)<21){D9 d=new D9();int size=Size(c),mass=Mass(c,size);for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)d.Add(SecondDist(Remove(c,i),s1,h2+i+1,a2||i==0,false),p);}actions["hit"]=Result(d);}}}
        var output=new Dictionary<string,object>{{"status","available"},{"actions",actions},{"nodes",nodes},{"elapsed_seconds",watch.Elapsed.TotalSeconds},{"caches",new int[]{dealerCache.Count,endCache.Count,secondCache.Count,firstCache.Count,secondDistCache.Count,firstDistCache.Count}},{"peak_memory",Process.GetCurrentProcess().PeakWorkingSet64}};
        if(probabilities!=null)foreach(var item in probabilities)output[item.Key]=item.Value;
        return output;
    }
    static int Main() {
        var serializer=new JavaScriptSerializer();SplitEngine engine=null;
        try {var input=(Dictionary<string,object>)serializer.DeserializeObject(Console.In.ReadToEnd());
            engine=new SplitEngine(Convert.ToInt32(input["dealer_up"]),(bool)input["peek_negative"],(bool)input["split_aces"],Convert.ToDouble(input["budget_seconds"]));
            Console.WriteLine(serializer.Serialize(engine.Solve(input)));return 0;
        } catch(Exception error){Console.WriteLine(serializer.Serialize(new Dictionary<string,object>{{"status","failed"},{"error",error.Message},{"nodes",engine==null?0:engine.nodes},{"elapsed_seconds",engine==null?0:engine.watch.Elapsed.TotalSeconds}}));return 1;}
    }
}
