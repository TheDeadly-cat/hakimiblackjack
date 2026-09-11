// Exact shared-shoe solver. Managed acceleration; no sampling or probability truncation.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.CompilerServices;
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
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public void Add(D7 d,double p) { B+=d.B*p; S17+=d.S17*p; S18+=d.S18*p; S19+=d.S19*p; S20+=d.S20*p; S21+=d.S21*p; Bust+=d.Bust*p; }
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public void End(int score,double p) { if(score>21) Bust+=p; else switch(score) {case 17:S17+=p;break;case 18:S18+=p;break;case 19:S19+=p;break;case 20:S20+=p;break;case 21:S21+=p;break;} }
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public double Get(int i) { switch(i) {case 0:return B;case 1:return S17;case 2:return S18;case 3:return S19;case 4:return S20;case 5:return S21;default:return Bust;} }
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public double Value(int score) {
        if(score>21) return -1;
        double v=Bust-B;
        if(score>17) v+=S17; else if(score<17) v-=S17;
        if(score>18) v+=S18; else if(score<18) v-=S18;
        if(score>19) v+=S19; else if(score<19) v-=S19;
        if(score>20) v+=S20; else if(score<20) v-=S20;
        if(score<21) v-=S21;
        return v;
    }
}

// A replacement cache only re-evaluates collisions; it never omits a branch.
class DealerMemo {
    const int Bits=22, Mask=(1<<Bits)-1;
    readonly ulong[] keys=new ulong[1<<Bits];
    readonly byte[] states=new byte[1<<Bits];
    readonly D7[] values=new D7[1<<Bits];
    public int Count;
    int Slot(Key key) {return key.GetHashCode()&Mask;}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
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

// 5x5 joint over per-hand nets {-2,-1,0,1,2}. Inline fields, no heap array.
struct D25 {
    public double
        A,B,C,D,E,
        F,G,H,I,J,
        K,L,M,N,O,
        P,Q,R,S,T,
        U,V,W,X,Y;
    public static D25 Zero() { return new D25(); }
    public double Slot(int i) {
        switch(i){
            case 0:return A;case 1:return B;case 2:return C;case 3:return D;case 4:return E;
            case 5:return F;case 6:return G;case 7:return H;case 8:return I;case 9:return J;
            case 10:return K;case 11:return L;case 12:return M;case 13:return N;case 14:return O;
            case 15:return P;case 16:return Q;case 17:return R;case 18:return S;case 19:return T;
            case 20:return U;case 21:return V;case 22:return W;case 23:return X;default:return Y;}
    }
    public void Put(int a,int b,double p) {
        int i=(a+2)*5+(b+2);
        switch(i){
            case 0:A+=p;break;case 1:B+=p;break;case 2:C+=p;break;case 3:D+=p;break;case 4:E+=p;break;
            case 5:F+=p;break;case 6:G+=p;break;case 7:H+=p;break;case 8:I+=p;break;case 9:J+=p;break;
            case 10:K+=p;break;case 11:L+=p;break;case 12:M+=p;break;case 13:N+=p;break;case 14:O+=p;break;
            case 15:P+=p;break;case 16:Q+=p;break;case 17:R+=p;break;case 18:S+=p;break;case 19:T+=p;break;
            case 20:U+=p;break;case 21:V+=p;break;case 22:W+=p;break;case 23:X+=p;break;default:Y+=p;break;}
    }
    public void Add(D25 o,double p) {
        if(o.A!=0)A+=o.A*p;if(o.B!=0)B+=o.B*p;if(o.C!=0)C+=o.C*p;if(o.D!=0)D+=o.D*p;if(o.E!=0)E+=o.E*p;
        if(o.F!=0)F+=o.F*p;if(o.G!=0)G+=o.G*p;if(o.H!=0)H+=o.H*p;if(o.I!=0)I+=o.I*p;if(o.J!=0)J+=o.J*p;
        if(o.K!=0)K+=o.K*p;if(o.L!=0)L+=o.L*p;if(o.M!=0)M+=o.M*p;if(o.N!=0)N+=o.N*p;if(o.O!=0)O+=o.O*p;
        if(o.P!=0)P+=o.P*p;if(o.Q!=0)Q+=o.Q*p;if(o.R!=0)R+=o.R*p;if(o.S!=0)S+=o.S*p;if(o.T!=0)T+=o.T*p;
        if(o.U!=0)U+=o.U*p;if(o.V!=0)V+=o.V*p;if(o.W!=0)W+=o.W*p;if(o.X!=0)X+=o.X*p;if(o.Y!=0)Y+=o.Y*p;
    }
    public double EV() {
        double e=0;
        for(int a=-2;a<=2;a++) for(int b=-2;b<=2;b++) e+=(a+b)*Slot((a+2)*5+(b+2));
        return e;
    }
}

class SplitEngine {
    const double Epsilon=1e-12;
    readonly int up,excluded; bool aces; readonly double budget;
    readonly Stopwatch watch=Stopwatch.StartNew();
    long nodes,informationBoundPrunes;
    int h2; bool a2,force2;
    readonly DealerMemo dealerCache=new DealerMemo();
    readonly Dictionary<ulong,D7> endCache=new Dictionary<ulong,D7>(1<<18);
    readonly Dictionary<Key,double> secondCache=new Dictionary<Key,double>(1<<20);
    readonly Dictionary<Key,double> firstCache=new Dictionary<Key,double>();
    readonly Dictionary<Key,D9> secondDistCache=new Dictionary<Key,D9>();
    readonly Dictionary<Key,D9> firstDistCache=new Dictionary<Key,D9>();
    readonly Dictionary<Key,double> secondDasEv=new Dictionary<Key,double>(1<<20);
    readonly Dictionary<Key,double> firstDasEv=new Dictionary<Key,double>();
    readonly Dictionary<Key,D25> secondDasDist=new Dictionary<Key,D25>();
    readonly Dictionary<Key,D25> firstDasDist=new Dictionary<Key,D25>();
    readonly Dictionary<Key,int> firstDasChoice=new Dictionary<Key,int>();
    readonly Dictionary<Key,int> secondDasChoice=new Dictionary<Key,int>(1<<20);
    int stake2, cards1, cards2; bool secondForceDeal, secondCanDas;
    public SplitEngine(int dealerUp,bool peek,bool splitAces,double seconds) {up=dealerUp;excluded=peek&&up==1?9:peek&&up==10?0:-1;aces=splitAces;budget=seconds;}
    void Check() {nodes++;if((nodes&1023)==1 && watch.Elapsed.TotalSeconds>=budget)throw new InvalidOperationException("TIMEOUT");}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int Score(int h,bool a) {return a&&h<=11?h+10:h;}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int Shift(int i) {return i*6;}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int Count(ulong c,int i) {return (int)((c>>Shift(i))&(i==9?255UL:63UL));}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static ulong Remove(ulong c,int i) {return c-(1UL<<Shift(i));}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int Size(ulong c) {return (int)((c&63UL)+((c>>6)&63UL)+((c>>12)&63UL)+((c>>18)&63UL)+((c>>24)&63UL)+((c>>30)&63UL)+((c>>36)&63UL)+((c>>42)&63UL)+((c>>48)&63UL)+(c>>54));}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static bool SafeLow(int size,int h,bool a) {return size>=64 && h<=11 && Score(h,a)<17;}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    int Mass(ulong c,int size) {return size-(excluded>=0?Count(c,excluded):0);}
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
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
        if(h>=14){Check();return HardTail(c,h,Size(c));}a=a&&h<=11;Key key=new Key(c,h*2+(a?1:0));D7 found;
        if(dealerCache.TryGetValue(key,out found))return found;
        Check();int size=Size(c);if(size==0)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        D7 result=new D7();
        for(int i=0;i<10;i++){int n=Count(c,i);if(n==0)continue;double p=(double)n/size;int nh=h+i+1;bool na=a||i==0;int score=Score(nh,na);
            if(score>=17)result.End(score,p);else result.Add(DealerRun(Remove(c,i),nh,na),p);}
        dealerCache[key]=result;return result;
    }
    D7 Dealer(ulong c) {
        D7 found;if(endCache.TryGetValue(c,out found))return found;
        Check();int size=Size(c),mass=Mass(c,size);if(mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");D7 result=new D7();
        for(int i=0;i<10;i++){int n=Count(c,i);if(n==0||i==excluded)continue;double p=(double)n/mass;
            if((up==1&&i==9)||(up==10&&i==0)){result.B+=p;continue;}
            int h=up+i+1;bool a=up==1||i==0;int score=Score(h,a);
            if(score>=17)result.End(score,p);else result.Add(DealerRun(Remove(c,i),h,a),p);}
        endCache[c]=result;return result;
    }
    double Stand(ulong c,int score) {return score>21?-1:Dealer(c).Value(score);}
    double Second(ulong c,int h,bool a,bool force) {
        if(h>21)return -1;a=a&&h<=11;Key key=new Key(c,h*4+(a?2:0)+(force?1:0));double value;if(secondCache.TryGetValue(key,out value))return value;
        Check();
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
    // Positive upper bounds are rounded outward, never toward an optimistic value.
    // See docs/V0.2b1数学与性能契约.md: one-card coupling + stopped draw-count bound.
    static double Up(double value) {return value==0?0:BitConverter.Int64BitsToDouble(BitConverter.DoubleToInt64Bits(value)+1);}
    double FutureInformationGainBound(ulong c,int h,int size,int mass) {
        return CoupledInformationGainBound(c,h,size,mass,2);
    }
    // Coupling-failure diameter is the later hand's remaining payoff span:
    // 2 without DAS, or 2*S2 when that hand's stake may still be 1 or 2.
    double CoupledInformationGainBound(ulong c,int h,int size,int mass,int span) {
        int k=21-h;
        int visible=Math.Max(0,21-h2)+Math.Max(0,16-up);
        int removed=k+visible+1,denominator=size-removed;
        if(k<=0)return 0;
        if(size<64||denominator<=0||mass<=k)return span;
        int left=removed;double[] probability=new double[10];
        for(int i=9;i>=0;i--){int n=Count(c,i),take=Math.Min(n,left);left-=take;probability[i]=Up((double)(n-take)/denominator);}
        double[] player=new double[31],dealer=new double[27];
        for(int t=20;t>=0;t--){player[t]=1;for(int i=0;i<10;i++)if(t+i+1<21&&probability[i]>0)player[t]=Up(player[t]+Up(probability[i]*player[t+i+1]));}
        for(int t=16;t>=0;t--){dealer[t]=1;for(int i=0;i<10;i++)if(t+i+1<17&&probability[i]>0)dealer[t]=Up(dealer[t]+Up(probability[i]*dealer[t+i+1]));}
        double expectedVisible=Up(player[h2]+dealer[up+1]);
        double one=Up(span*Up(Up(1.0/(mass-k))+Up(expectedVisible/denominator)));
        return Math.Min(span,Up(one*player[h]));
    }
    int DasSecondMaxStake() {
        if(aces) return 1;
        if(stake2==2||secondCanDas) return 2;
        if(secondForceDeal&&cards2<=1&&stake2==1) return 2;
        return 1;
    }
    double FirstStand(ulong c,int h,bool a) {return Stand(c,Score(h,a))+Second(c,h2,a2,force2);}
    double First(ulong c,int h,bool a,bool force) {
        a=a&&h<=11;Key key=new Key(c,h*4+(a?2:0)+(force?1:0));double value;if(firstCache.TryGetValue(key,out value))return value;
        Check();
        int size=Size(c),mass=Mass(c,size),score=Score(h,a);
        bool safe=!aces&&SafeLow(size,h,a);
        if(!force&&!safe){value=FirstStand(c,h,a);if(aces||score>=21||size<2){firstCache[key]=value;return value;}
            double bust=0;for(int i=0;i<10;i++)if(Score(h+i+1,a||i==0)>21)bust+=Draw(c,i,size,mass);
            if(2-2*bust<=value+Epsilon){firstCache[key]=value;return value;}
            if(h>=12&&size>=64){double gain=FutureInformationGainBound(c,h,size,mass),own=Stand(c,score);
                if(1-2*bust+gain<=own+Epsilon){informationBoundPrunes++;firstCache[key]=value;return value;}
                double ownHit=0;for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)ownHit+=p*Second(Remove(c,i),h+i+1,a||i==0,false);}
                if(ownHit+gain<=own+Epsilon){informationBoundPrunes++;firstCache[key]=value;return value;}}
}
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
        a=a&&h<=11;Key key=new Key(c,(h*4+(a?2:0)+(force?1:0))*32+s1);D9 result;
        if(secondDistCache.TryGetValue(key,out result))return result;
        Check();
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        bool hit=force||(!aces&&SafeLow(size,h,a))||(!aces&&score<21&&size>=2&&Second(c,h,a,false)>Stand(c,score)+Epsilon);
        if(!hit)result=Terminal(c,s1,Math.Min(score,22));else{
            if(size<2||mass<=0)throw new InvalidOperationException("INSUFFICIENT_CARDS");result=new D9();
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)result.Add(SecondDist(Remove(c,i),s1,h+i+1,a||i==0,false),p);}}
        secondDistCache[key]=result;return result;
    }
    D9 FirstDist(ulong c,int h,bool a,bool force) {
        a=a&&h<=11;Key key=new Key(c,h*4+(a?2:0)+(force?1:0));D9 result;if(firstDistCache.TryGetValue(key,out result))return result;
        Check();
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
    static int DasState(int h,bool a,bool forceDeal,bool forceClose,bool canDas,int stake1,int stake2,int s1,int nCards) {
        if(!canDas&&!forceDeal) nCards=0;
        return (h&63)|((a?1:0)<<6)|((forceDeal?1:0)<<7)|((forceClose?1:0)<<8)|((canDas?1:0)<<9)
            |((stake1&3)<<10)|((stake2&3)<<12)|((s1&31)<<14)|((nCards&15)<<19);
    }
    double ClosedFirstPlusSecond(ulong c,int s1,int stake1) {
        return (double)stake1*Stand(c,s1)+SecondDas(c,22,0,h2,a2,stake2,secondForceDeal,false,secondCanDas,cards2);
    }
    static bool CanDasAfterDeal(int nCards,int ns,int stake) {
        return nCards==1&&ns<21&&stake==1;
    }
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int StakePay(int score,int dealerIndex,int stake) {
        int unit=score>21||dealerIndex==0?-1:dealerIndex==6||score>dealerIndex+16?1:score==dealerIndex+16?0:-1;
        return unit*stake;
    }
    D25 TerminalDas(ulong c,int s1,int s2,int stake1,int stake2) {
        Check(); D25 result=D25.Zero();
        if(s1>21&&s2>21){result.Put(-stake1,-stake2,1); return result;}
        D7 dealer=Dealer(c);
        for(int i=0;i<7;i++){double p=dealer.Get(i);if(p==0)continue;
            result.Put(StakePay(s1,i,stake1),StakePay(s2,i,stake2),p);}
        return result;
    }
    D25 FromD9(D9 d,int stake1) {
        D25 result=D25.Zero();
        for(int i=0;i<9;i++) result.Put(stake1*(i/3-1),i%3-1,d.Get(i));
        return result;
    }
    object DasResult(D25 d) {
        var net=new Dictionary<string,double>(); for(int v=-4;v<=4;v++) net[v.ToString()]=0;
        var joint=new Dictionary<string,double>(); double one=0,two=0;
        for(int a=-2;a<=2;a++) for(int b=-2;b<=2;b++) {
            double p=d.Slot((a+2)*5+(b+2)); net[(a+b).ToString()]+=p; joint[a+","+b]=p; one+=a*p; two+=b*p;
        }
        return new Dictionary<string,object>{{"ev",d.EV()},{"net_distribution",net},{"joint_distribution",joint},{"hand_evs",new double[]{one,two}}};
    }
    double SecondDas(ulong c,int s1,int stake1,int h,bool a,int st2,bool forceDeal,bool forceClose,bool canDas,int nCards) {
        if(h>21) return TerminalDas(c,s1,22,stake1,st2).EV();
        a=a&&h<=11; Key key=new Key(c,DasState(h,a,forceDeal,forceClose,canDas,stake1,st2,s1,nCards));
        double value; if(secondDasEv.TryGetValue(key,out value)) return value;
        Check();
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        if(forceClose) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            value=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) value+=p*TerminalDas(Remove(c,i),s1,Math.Min(Score(h+i+1,a||i==0),22),stake1,st2).EV();}
            secondDasChoice[key]=4; secondDasEv[key]=value; return value;
        }
        if(forceDeal) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            value=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p==0) continue;
                int nh=h+i+1; bool na=a||i==0; int ns=Score(nh,na);
                value+=p*SecondDas(Remove(c,i),s1,stake1,nh,na,st2,false,false,!aces&&CanDasAfterDeal(nCards,ns,st2),nCards+1);}
            secondDasChoice[key]=3; secondDasEv[key]=value; return value;
        }
        if(!canDas&&st2==1){
            int sc=score; double own=(stake1==0||s1>21)?0:(double)stake1*Stand(c,s1);
            double sec=Second(c,h,a,false);
            value=own+sec;
            bool hit=!aces&&sc<21&&size>=2&&(SafeLow(size,h,a)||sec>Stand(c,sc)+Epsilon);
            secondDasChoice[key]=hit?1:0; secondDasEv[key]=value; return value;
        }
        double standing=TerminalDas(c,s1,Math.Min(score,22),stake1,st2).EV();
        if(aces||score>=21||size<2){secondDasChoice[key]=0; secondDasEv[key]=standing; return standing;}
        bool lowHit=!aces&&SafeLow(size,h,a);
        bool skipHit=false;
        if(!lowHit){double bust=0; for(int i=0;i<10;i++) if(Score(h+i+1,a||i==0)>21) bust+=Draw(c,i,size,mass);
            if((double)st2*(1-2*bust)<=(double)st2*Stand(c,score)+Epsilon) skipHit=true;}
        if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
        double hitting=0;
        if(!skipHit) for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) hitting+=p*SecondDas(Remove(c,i),s1,stake1,h+i+1,a||i==0,st2,false,false,false,nCards+1);}
        if(lowHit||(!skipHit&&hitting>standing+Epsilon)){value=hitting; secondDasChoice[key]=1;}
        else {value=standing; secondDasChoice[key]=0;}
        if(canDas){double dbl=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) dbl+=p*TerminalDas(Remove(c,i),s1,Math.Min(Score(h+i+1,a||i==0),22),stake1,2).EV();}
            if(dbl>value+Epsilon){value=dbl; secondDasChoice[key]=2;}}
        secondDasEv[key]=value; return value;
    }
    double FirstDas(ulong c,int h,bool a,int stake1,bool forceDeal,bool forceClose,bool canDas,int nCards) {
        a=a&&h<=11; Key key=new Key(c,DasState(h,a,forceDeal,forceClose,canDas,stake1,stake2,0,nCards));
        double value; if(firstDasEv.TryGetValue(key,out value)) return value;
        Check();
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        if(forceClose) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            value=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                value+=p*ClosedFirstPlusSecond(Remove(c,i),Math.Min(Score(h+i+1,a||i==0),22),stake1);}
            firstDasChoice[key]=4; firstDasEv[key]=value; return value;
        }
        if(forceDeal) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            value=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p==0) continue;
                int nh=h+i+1; bool na=a||i==0; int ns=Score(nh,na);
                value+=p*FirstDas(Remove(c,i),nh,na,stake1,false,false,!aces&&CanDasAfterDeal(nCards,ns,stake1),nCards+1);}
            firstDasChoice[key]=3; firstDasEv[key]=value; return value;
        }
        if(aces||score>=21||size<2){
            value=ClosedFirstPlusSecond(c,Math.Min(score,22),stake1);
            firstDasChoice[key]=0; firstDasEv[key]=value; return value;
        }
        bool lowHit=!aces&&SafeLow(size,h,a);
        if(lowHit){
            FirstDistDas(c,h,a,stake1,false,false,canDas,nCards);
            return firstDasEv[key];
        }
        double standing=ClosedFirstPlusSecond(c,Math.Min(score,22),stake1);
        bool skipHit=false;
        double bust=0; for(int i=0;i<10;i++) if(Score(h+i+1,a||i==0)>21) bust+=Draw(c,i,size,mass);
        int s2=DasSecondMaxStake();
        if((double)stake1*(1-2*bust)+s2<=standing+Epsilon) skipHit=true;
        else if(h>=12&&size>=64){
            double gain=CoupledInformationGainBound(c,h,size,mass,2*s2),own=(double)stake1*Stand(c,score);
            if((double)stake1*(1-2*bust)+gain<=own+Epsilon){informationBoundPrunes++; skipHit=true;}
            else if(-(double)stake1+gain<=own+Epsilon){
                double ownHit=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) ownHit+=p*Second(Remove(c,i),h+i+1,a||i==0,false);}
                if((double)stake1*ownHit+gain<=own+Epsilon){informationBoundPrunes++; skipHit=true;}
            }
        }
        if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
        double hitting=0;
        if(!skipHit) for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) hitting+=p*FirstDas(Remove(c,i),h+i+1,a||i==0,stake1,false,false,false,nCards+1);}
        if(!skipHit&&hitting>standing+Epsilon){value=hitting; firstDasChoice[key]=1;}
        else {value=standing; firstDasChoice[key]=0;}
        if(canDas){double dbl=0; for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
            dbl+=p*ClosedFirstPlusSecond(Remove(c,i),Math.Min(Score(h+i+1,a||i==0),22),2);}
            if(dbl>value+Epsilon){value=dbl; firstDasChoice[key]=2;}}
        firstDasEv[key]=value; return value;
    }
    D25 SecondDistDas(ulong c,int s1,int stake1,int h,bool a,int st2,bool forceDeal,bool forceClose,bool canDas,int nCards) {
        a=a&&h<=11; Key key=new Key(c,DasState(h,a,forceDeal,forceClose,canDas,stake1,st2,s1,nCards));
        D25 result; if(secondDasDist.TryGetValue(key,out result)) return result;
        Check();
        if(h>21){result=TerminalDas(c,s1,22,stake1,st2); secondDasDist[key]=result; return result;}
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        if(forceClose) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            result=D25.Zero();
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                result.Add(TerminalDas(Remove(c,i),s1,Math.Min(Score(h+i+1,a||i==0),22),stake1,st2),p);}
            secondDasDist[key]=result; return result;
        }
        if(forceDeal) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            result=D25.Zero();
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p==0) continue;
                int nh=h+i+1; bool na=a||i==0; int ns=Score(nh,na);
                result.Add(SecondDistDas(Remove(c,i),s1,stake1,nh,na,st2,false,false,!aces&&CanDasAfterDeal(nCards,ns,st2),nCards+1),p);}
            secondDasDist[key]=result; return result;
        }
        // Closed first hand: unit second tree is b1 SecondDist, then scale the first axis.
        if(!canDas&&st2==1){
            result=FromD9(SecondDist(c,s1>21?22:s1,h,a,false),stake1);
            secondDasDist[key]=result; return result;
        }
        int choice=0;
        SecondDas(c,22,0,h,a,st2,false,false,canDas,nCards);
        secondDasChoice.TryGetValue(new Key(c,DasState(h,a,false,false,canDas,0,st2,22,nCards)),out choice);
        if(aces||score>=21||size<2||choice==0){
            result=TerminalDas(c,s1,Math.Min(score,22),stake1,st2);
            secondDasDist[key]=result; return result;
        }
        if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
        result=D25.Zero();
        if(choice==2){
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                result.Add(TerminalDas(Remove(c,i),s1,Math.Min(Score(h+i+1,a||i==0),22),stake1,2),p);}
        } else {
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                result.Add(SecondDistDas(Remove(c,i),s1,stake1,h+i+1,a||i==0,st2,false,false,false,nCards+1),p);}
        }
        secondDasDist[key]=result; return result;
    }
    D25 FirstDistDas(ulong c,int h,bool a,int stake1,bool forceDeal,bool forceClose,bool canDas,int nCards) {
        a=a&&h<=11; Key key=new Key(c,DasState(h,a,forceDeal,forceClose,canDas,stake1,stake2,0,nCards));
        D25 result; if(firstDasDist.TryGetValue(key,out result)) return result;
        Check();
        int score=Score(h,a),size=Size(c),mass=Mass(c,size);
        if(forceClose) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            result=D25.Zero(); for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                result.Add(SecondDistDas(Remove(c,i),Math.Min(Score(h+i+1,a||i==0),22),stake1,h2,a2,stake2,secondForceDeal,false,secondCanDas,cards2),p);}
            firstDasDist[key]=result; return result;
        }
        if(forceDeal) {
            if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
            result=D25.Zero(); for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p==0) continue;
                int nh=h+i+1; bool na=a||i==0; int ns=Score(nh,na);
                result.Add(FirstDistDas(Remove(c,i),nh,na,stake1,false,false,!aces&&CanDasAfterDeal(nCards,ns,stake1),nCards+1),p);}
            firstDasDist[key]=result; return result;
        }
        if(aces||score>=21||size<2){
            result=SecondDistDas(c,Math.Min(score,22),stake1,h2,a2,stake2,secondForceDeal,false,secondCanDas,cards2);
            firstDasChoice[key]=0; firstDasEv[key]=result.EV(); firstDasDist[key]=result; return result;
        }
        if(size<2||mass<=0) throw new InvalidOperationException("INSUFFICIENT_CARDS");
        bool lowHit=!aces&&SafeLow(size,h,a);
        if(lowHit){
            result=D25.Zero();
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                result.Add(FirstDistDas(Remove(c,i),h+i+1,a||i==0,stake1,false,false,false,nCards+1),p);}
            int picked=1;
            if(canDas){
                double hitEv=result.EV(),dblEv=0;
                for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                    dblEv+=p*ClosedFirstPlusSecond(Remove(c,i),Math.Min(Score(h+i+1,a||i==0),22),2);}
                if(dblEv>hitEv+Epsilon){
                    result=D25.Zero();
                    for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0){
                        int ns=Math.Min(Score(h+i+1,a||i==0),22);
                        result.Add(SecondDistDas(Remove(c,i),ns,2,h2,a2,stake2,secondForceDeal,false,secondCanDas,cards2),p);}
                    }
                    picked=2;
                }
            }
            firstDasChoice[key]=picked; firstDasEv[key]=result.EV(); firstDasDist[key]=result; return result;
        }
        int choice=0;
        FirstDas(c,h,a,stake1,false,false,canDas,nCards);
        firstDasChoice.TryGetValue(key,out choice);
        if(choice==0){
            result=SecondDistDas(c,Math.Min(score,22),stake1,h2,a2,stake2,secondForceDeal,false,secondCanDas,cards2);
            firstDasDist[key]=result; return result;
        }
        result=D25.Zero();
        if(choice==2){
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0){
                int ns=Math.Min(Score(h+i+1,a||i==0),22);
                result.Add(SecondDistDas(Remove(c,i),ns,2,h2,a2,stake2,secondForceDeal,false,secondCanDas,cards2),p);}
            }
        } else {
            for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                result.Add(FirstDistDas(Remove(c,i),h+i+1,a||i==0,stake1,false,false,false,nCards+1),p);}
        }
        firstDasDist[key]=result; return result;
    }
    void FillDasActions(ulong c,int[] one,int[] two,int h1,bool a1,int active,bool forceActive,bool forceClose,int stake1,int st2,Dictionary<string,object> actions) {
        stake2=st2;
        cards1=one.Length; cards2=two.Length;
        secondForceDeal=two.Length==1||(active==1&&forceActive&&!forceClose);
        secondCanDas=!aces&&two.Length==2&&Score(h2,a2)<21&&st2==1&&!(active==1&&forceClose);
        bool firstForceDeal=one.Length==1||(active==0&&forceActive&&!forceClose);
        bool firstCanDas=!aces&&one.Length==2&&Score(h1,a1)<21&&stake1==1&&!forceClose;
        if(active==2) actions["complete"]=DasResult(TerminalDas(c,Math.Min(Score(h1,a1),22),Math.Min(Score(h2,a2),22),stake1,st2));
        else if(active==0) {
            if(firstForceDeal||forceClose) actions["deal"]=DasResult(FirstDistDas(c,h1,a1,stake1,firstForceDeal,forceClose,firstCanDas,cards1));
            else {
                actions["stand"]=DasResult(SecondDistDas(c,Math.Min(Score(h1,a1),22),stake1,h2,a2,st2,secondForceDeal,false,secondCanDas,cards2));
                if(!aces&&Score(h1,a1)<21) {
                    int size=Size(c),mass=Mass(c,size); D25 hit=D25.Zero();
                    for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) hit.Add(FirstDistDas(Remove(c,i),h1+i+1,a1||i==0,stake1,false,false,false,cards1+1),p);}
                    actions["hit"]=DasResult(hit);
                    if(firstCanDas){D25 dbl=D25.Zero();
                        for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                            dbl.Add(SecondDistDas(Remove(c,i),Math.Min(Score(h1+i+1,a1||i==0),22),2,h2,a2,st2,secondForceDeal,false,secondCanDas,cards2),p);}
                        actions["double"]=DasResult(dbl);}
                }
            }
        } else {
            int s1=Math.Min(Score(h1,a1),22);
            if(secondForceDeal||forceClose) actions["deal"]=DasResult(SecondDistDas(c,s1,stake1,h2,a2,st2,secondForceDeal,forceClose,secondCanDas,cards2));
            else {
                actions["stand"]=DasResult(TerminalDas(c,s1,Math.Min(Score(h2,a2),22),stake1,st2));
                if(!aces&&Score(h2,a2)<21) {
                    int size=Size(c),mass=Mass(c,size); D25 hit=D25.Zero();
                    for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0) hit.Add(SecondDistDas(Remove(c,i),s1,stake1,h2+i+1,a2||i==0,st2,false,false,false,cards2+1),p);}
                    actions["hit"]=DasResult(hit);
                    if(secondCanDas){D25 dbl=D25.Zero();
                        for(int i=0;i<10;i++){double p=Draw(c,i,size,mass); if(p>0)
                            dbl.Add(TerminalDas(Remove(c,i),s1,Math.Min(Score(h2+i+1,a2||i==0),22),stake1,2),p);}
                        actions["double"]=DasResult(dbl);}
                }
            }
        }
    }
    object Solve(Dictionary<string,object> input) {
        int[] counts=Ints(input["counts"]);if(counts.Length!=10)throw new ArgumentException("COUNT_LENGTH");ulong c=0;
        for(int i=0;i<10;i++){if(counts[i]<0||counts[i]>(i==9?128:32))throw new ArgumentException("COUNT_RANGE");c|=(ulong)counts[i]<<Shift(i);}
        object[] hs=(object[])input["hands"];int[] one=Ints(hs[0]),two=Ints(hs[1]);int h1=0;bool a1=false;h2=0;a2=false;
        foreach(int v in one){h1+=v;a1|=v==1;}foreach(int v in two){h2+=v;a2|=v==1;}force2=two.Length==1||(Convert.ToInt32(input["active"])==1&&input.ContainsKey("force_active")&&(bool)input["force_active"]);
        int active=Convert.ToInt32(input["active"]);var actions=new Dictionary<string,object>();
        if(active<2&&Size(c)<2)throw new InvalidOperationException("INSUFFICIENT_CARDS");
        bool allowDas=input.ContainsKey("allow_das")&&(bool)input["allow_das"];
        int stake1=1,st2=1;
        if(input.ContainsKey("stakes")){int[] st=Ints(input["stakes"]); if(st.Length!=2||(st[0]!=1&&st[0]!=2)||(st[1]!=1&&st[1]!=2)) throw new ArgumentException("STAKES"); stake1=st[0]; st2=st[1];}
        bool forceClose=input.ContainsKey("force_close")&&(bool)input["force_close"];
        bool forceActive=input.ContainsKey("force_active")&&(bool)input["force_active"];
        if(!allowDas){ if(forceClose) throw new ArgumentException("FORCE_CLOSE"); if(stake1!=1||st2!=1) throw new ArgumentException("STAKES"); }
        Dictionary<string,object> probabilities=null;
        if(input.ContainsKey("single_player")){
            object[] requested=(object[])input["single_actions"];
            probabilities=Single(c,Ints(input["single_player"]),requested,actions);
            if(Array.IndexOf(requested,"split")>=0){
                if(allowDas){ stake2=st2; cards1=one.Length; cards2=two.Length;
                    secondForceDeal=true; secondCanDas=false;
                    actions["split"]=DasResult(FirstDistDas(c,h1,a1,stake1,true,false,false,cards1)); }
                else actions["split"]=Result(FirstDist(c,h1,a1,true));
            }
        }
        else if(allowDas) FillDasActions(c,one,two,h1,a1,active,forceActive,forceClose,stake1,st2,actions);
        else if(active==2)actions["complete"]=Result(Terminal(c,Math.Min(Score(h1,a1),22),Math.Min(Score(h2,a2),22)));
        else if(active==0){if(one.Length==1||(input.ContainsKey("force_active")&&(bool)input["force_active"]))actions["deal"]=Result(FirstDist(c,h1,a1,true));else{
            actions["stand"]=Result(SecondDist(c,Math.Min(Score(h1,a1),22),h2,a2,force2));
            if(!aces&&Score(h1,a1)<21){D9 d=new D9();int size=Size(c),mass=Mass(c,size);for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)d.Add(FirstDist(Remove(c,i),h1+i+1,a1||i==0,false),p);}actions["hit"]=Result(d);}}}
        else {int s1=Math.Min(Score(h1,a1),22);if(force2)actions["deal"]=Result(SecondDist(c,s1,h2,a2,true));else{
            actions["stand"]=Result(Terminal(c,s1,Math.Min(Score(h2,a2),22)));
            if(!aces&&Score(h2,a2)<21){D9 d=new D9();int size=Size(c),mass=Mass(c,size);for(int i=0;i<10;i++){double p=Draw(c,i,size,mass);if(p>0)d.Add(SecondDist(Remove(c,i),s1,h2+i+1,a2||i==0,false),p);}actions["hit"]=Result(d);}}}
        var output=new Dictionary<string,object>{{"status","available"},{"actions",actions},{"nodes",nodes},{"information_bound_prunes",informationBoundPrunes},{"elapsed_seconds",watch.Elapsed.TotalSeconds},{"caches",new int[]{dealerCache.Count,endCache.Count,secondCache.Count,firstCache.Count,secondDistCache.Count,firstDistCache.Count}},{"peak_memory",Process.GetCurrentProcess().PeakWorkingSet64}};
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
